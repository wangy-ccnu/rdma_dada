"""Actual installed-bundle runtime, Python 3.8; internal B3 factory interface.

PipelineRuntime(config, retained_supervisor, *, journal=None) consumes the B3
trusted deployment. runtime has kind='pipeline', binaries, environment,
ready_timeout_seconds, drain_timeout_seconds, poll_interval_seconds and ipc.
Each binary is {path, sha256, leaf}: leaf is a reviewed, hash-bound no-fork
contract (numactl must exec). Missing descendant proof never permits release.
ipc is the verified PSRDADA build contract {max_readers, header_buffers,
exclusive_create}. It is not client input or an Observation schema extension.
Installed bundle instances and exact B1 resource declarations are mandatory.

prepare/start/stop return {sequence,state,cleanup,evidence,error,outcome};
observe omits outcome. Evidence has exactly the three MS06 boolean fields.
Optional action_outcomes resolves only intents captured on the journal-owning
thread at an actual effect. Optional cleanup_evidence exactly attests the B1
owner/resources and processes_gone/descendants_gone/resources_gone=True.
The scheduler never touches SQLite. Reports are detached under a per-owner lock;
unchanged cached facts retain their sequence. Restart does not adopt children.

prepare starts consumers then the receiver, which can receive before the UTC
boundary; the immutable plan rejects records outside its data timeline. start
arms the ORIGINAL UTC/duration. The autonomous scheduler closes ingress at its
original end and drains to actual EOD. Clock/exit0 alone prove no data progress.
Private run directories and compiler diagnostics are retained as evidence.
An exact trusted claim is captured before bundle/host/compiler validation.
Fresh preparation records allocation_attempted=False until the first pipeline launch;
this positive no-effect history permits claim release after pre-allocation
refusal without asserting that foreign resources at those keys are gone.
The compiler is independently supervised with a deadline and bounded output;
it must be reaped under its reviewed no-fork contract before that release.
Unknown/reopened ownership and any possibly partial creation remain UNKNOWN.
No shell, process-group kill, key-based destroy, CUDA ordinal remapping, default
binary directory, remote execution or journal release occurs in this module.
"""
import copy
import ctypes
import datetime
from decimal import Decimal
import hashlib
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time

from .bundle import _canonical, _read_json, _safe_path, _sha_file
from .compiler import resolve_instance
from .deployment import validate_deployment
from .model import validate_global
from .ownership import _owner, _resources
from .processes import _private, ProcessError


FACTS = ('target_data_complete', 'output_published', 'eod_complete')
TERMINAL = {'COMPLETED', 'FAILED', 'INTERRUPTED'}
ROLES = ('sink', 'gpu', 'unpack', 'receiver')
COMPILER_OUTPUT_LIMIT = 1024 * 1024  # Per stream; full private B2 logs are retained.


def _positive(value, name, maximum=60):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError('invalid ' + name)
    return value


def _utc(text):
    return datetime.datetime.strptime(text, '%Y-%m-%d-%H:%M:%S').replace(tzinfo=datetime.timezone.utc)


def _sync_bytes(ipc):
    """Native ipcsync_t ABI of the explicitly verified PSRDADA build."""
    u=ctypes.c_uint64; readers=ipc['max_readers']; transfers=ipc['header_buffers']
    class Sync(ctypes.Structure):
        _fields_=[('semkey_connect',ctypes.c_int),('semkey_data',ctypes.c_int*readers),
                  ('nbufs',u),('bufsz',u),('w_buf_next',u),('w_buf_curr',u),
                  ('w_state',ctypes.c_int),('w_xfer',u),('r_bufs',u*readers),
                  ('r_states',ctypes.c_int*readers),('r_xfers',u*readers),
                  ('n_readers',ctypes.c_uint),('s_buf',u*transfers),('s_byte',u*transfers),
                  ('eod',ctypes.c_char*transfers),('e_buf',u*transfers),('e_byte',u*transfers),
                  ('on_device_id',ctypes.c_int)]
    return ctypes.sizeof(Sync)


def ring_footprint(ring, ipc):
    """Exact one-reader PSRDADA families, including the compiled MAX reader gap.

    Semaphores are allocated for n_readers=1, but buffer keys skip ALL
    IPCBUF_READERS slots. Header uses IPCBUF_XFERS buffers, not data nbufs.
    """
    result = []
    key = int(ring['key'], 16)
    for base, count, size in ((key, ring['blocks'], ring['block_bytes']),
                              (key + 1, ipc['header_buffers'], ring['header_bytes'])):
        result += [dict(kind='shm', key=base, size=_sync_bytes(ipc)+count*(1+ctypes.sizeof(ctypes.c_int))),
                   dict(kind='sem', key=base+0x10000, nsems=2),
                   dict(kind='sem', key=base+0x20000, nsems=5)]
        result += [dict(kind='shm', key=base+(ipc['max_readers']+2+i)*0x10000, size=size)
                   for i in range(count)]
    if any(not 0 < item['key'] <= 0xffffffff for item in result):
        raise ValueError('IPC key family overflows uint32 key_t identity')
    return result


def _manifest(root):
    root = _safe_path(root)
    expected = {}
    for line in (root / 'MANIFEST.sha256').read_text(encoding='ascii').splitlines():
        sha, relative = line.split('  ', 1)
        path = _safe_path(root / relative)
        if (not relative or relative in expected or not path.is_file() or
                root not in path.parents or len(sha) != 64 or _sha_file(path) != sha):
            raise ValueError('BUNDLE_MANIFEST')
        expected[relative] = sha
    actual = set()
    for path in root.rglob('*'):
        _safe_path(path)
        if path.is_file() and path != root / 'MANIFEST.sha256':
            actual.add(path.relative_to(root).as_posix())
    if actual != set(expected):
        raise ValueError('BUNDLE_FILE_SET')
    return expected


class LinuxPlatform:
    """Narrow Linux OS boundary. Tests replace this, never launch real hardware.

    One-shot exclusive dada_db allocation avoids its key-reconnect destroy path.
    Destruction uses captured SysV object IDs, after immutable metadata checks.
    Failed/partial creation cannot establish semaphore creator ownership and is
    deliberately retained UNKNOWN; no -d or -w cleanup is attempted.
    """
    def __init__(self, binaries):
        self.binaries = binaries
        self.libc = ctypes.CDLL(None, use_errno=True)

    def check_host(self, profile, inventory):
        if sys.platform != 'linux':
            raise ValueError('Linux /proc PSRDADA runtime required')
        host = next(h for h in inventory['hosts'] if profile['server_id'] in h['aliases'])
        for role, cpus in profile['cpu'].items():
            for cpu in cpus:
                root = Path('/sys/devices/system/cpu/cpu' + str(cpu))
                expected = host['cpus'][str(cpu)]
                for field, name in (('physical_package_id', 'socket_id'), ('core_id', 'core_id')):
                    if int((root / 'topology' / field).read_text()) != expected[name]:
                        raise ValueError('live CPU topology mismatch')
                if not (root / ('node' + str(profile['numa'][role]))).exists():
                    raise ValueError('live CPU NUMA mismatch')
        nic = host['nics'][profile['flow']['interface']]
        if nic['verbs_port'] != 1:
            raise ValueError('only verified verbs port 1 is supported')
        port = Path('/sys/class/infiniband') / nic['rdma_device'] / 'ports/1'
        if not (port / 'state').read_text().strip().endswith('ACTIVE'):
            raise ValueError('RDMA port not active')
        address = (Path('/sys/class/net') / profile['flow']['interface'] / 'address').read_text().strip().lower()
        if address != nic['mac_address'].lower():
            raise ValueError('live NIC MAC mismatch')
        binary = self.binaries['gpu_probe']
        if _sha_file(_safe_path(binary['path'])) != binary['sha256']:
            raise ValueError('GPU probe binary changed')
        result = subprocess.run([binary['path'], '--query-gpu=index,uuid', '--format=csv,noheader'],
                                env={}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=5, check=False)
        if result.returncode != 0:
            raise ValueError('live GPU query failed')
        gpus = dict(line.strip().split(', ', 1) for line in result.stdout.splitlines())
        if gpus.get(str(profile['gpu_id'])) != profile['gpu_uuid']:
            raise ValueError('live GPU ordinal/UUID mismatch')

    @staticmethod
    def _objects():
        result = {}
        for kind in ('shm', 'sem'):
            lines = Path('/proc/sysvipc/' + kind).read_text().splitlines()
            names = lines[0].split()
            for line in lines[1:]:
                row = dict(zip(names, line.split()))
                row = {k: int(v, 8 if k == 'perms' else 10) for k, v in row.items()}
                row['key'] &= 0xffffffff
                result[(kind, row['key'])] = row
        return result

    def ensure_absent(self, footprint):
        objects = self._objects()
        if any((item['kind'], item['key']) in objects for item in footprint):
            raise ValueError('FOREIGN_IPC')

    @staticmethod
    def _fingerprint(kind, row):
        fields = ('key', kind+'id', 'uid', 'cuid', 'ctime', 'perms')
        fields += ('size', 'cpid') if kind == 'shm' else ('nsems',)
        return {k: row[k] for k in fields}

    def capture_ring(self, ring, report, footprint):
        if report['state'] != 'EXITED' or report['returncode'] != 0:
            raise ValueError('ring creation not authoritative')
        objects = self._objects()
        captured = []
        for item in footprint:
            row = objects.get((item['kind'], item['key']))
            if row is None or row['uid'] != os.getuid() or row['cuid'] != os.getuid():
                raise ValueError('ring ownership incomplete')
            if item['kind'] == 'shm':
                if row['cpid'] != report['identity']['pid'] or (item['size'] is not None and row['size'] != item['size']):
                    raise ValueError('ring creator/geometry mismatch')
            elif row['nsems'] != item['nsems']:
                raise ValueError('ring semaphore geometry mismatch')
            captured.append(dict(kind=item['kind'], metadata=self._fingerprint(item['kind'], row)))
        return dict(ring=ring, identity=report['identity'], objects=captured)

    def remove_ring(self, token):
        # Object-id operations cannot reconnect to a foreign object at the key.
        objects=self._objects()
        for item in token['objects']:
            kind,expected=item['kind'],item['metadata']
            row=objects.get((kind,expected['key']))
            if row is not None and (self._fingerprint(kind,row)!=expected or
                                    (kind=='shm' and row['nattch']!=0)):
                return False
        for item in token['objects']:
            kind, expected = item['kind'], item['metadata']
            row = self._objects().get((kind, expected['key']))
            if row is None:
                continue
            if self._fingerprint(kind, row) != expected:
                return False
            if kind == 'shm':
                if row['nattch'] != 0:
                    return False
                status = self.libc.shmctl(expected['shmid'], 0, None)  # Linux IPC_RMID
            else:
                status = self.libc.semctl(expected['semid'], 0, 0)
            if status != 0:
                return False
        remaining = self._objects()
        return all((item['kind'], item['metadata']['key']) not in remaining for item in token['objects'])

    @staticmethod
    def _record(path, report, ids):
        path = _safe_path(path)
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 65536:
            raise ValueError('unsafe lifecycle record')
        value = _read_json(path)
        if (type(value) is not dict or type(value.get('pid')) is not int or
                value.get('pid') != report['identity']['pid'] or
                any(type(value.get(k)) is not type(v) or value[k] != v for k, v in ids.items()) or
                type(value.get('schema_version')) is not int or value['schema_version'] != 1):
            raise ValueError('lifecycle record binding mismatch')
        return value

    def ready(self, role, report, directory, ids, ring):
        if role == 'sink':
            # dbnull has no ready-file option. Its mappings must contain this
            # exact ring's sync IDs and it must hold the sole reader lock.
            maps = Path('/proc/{}/maps'.format(report['identity']['pid'])).read_text()
            for item in ring['objects']:
                row = item['metadata']
                if item['kind'] == 'shm' and row['key'] in (int(ring['ring']['key'],16), int(ring['ring']['key'],16)+1):
                    if not any('/SYSV{:08x}'.format(row['key']) in line and
                               int(line.split()[4]) == row['shmid'] for line in maps.splitlines()):
                        return False
            header_key = int(ring['ring']['key'],16)+1+0x10000
            semaphore = next(i['metadata'] for i in ring['objects']
                             if i['kind']=='sem' and i['metadata']['key']==header_key)
            return self.libc.semctl(semaphore['semid'],1,12) == 0  # Linux GETVAL / IPCBUF_READ
        value = self._record(directory / (role+'.ready'), report, ids)
        if value is None:
            return False
        return value.get('input_key') == int(ring['key'],16)

    def result(self, role, report, directory, ids):
        return self._record(directory / (role+'.result'), report, ids)

    @staticmethod
    def descendants_gone(reports, binaries):
        # The proof is a reviewed no-fork contract for the EXACT pinned code,
        # plus authoritative retained-child reaps, never an empty /proc scan.
        return (all(b['leaf'] is True for b in binaries.values()) and
                all(r['state'] in ('EXITED','NOT_STARTED') and
                    (type(r['returncode']) is int or r['state']=='NOT_STARTED') for r in reports))


class PipelineRuntime:
    def __init__(self, config, supervisor, *, journal=None, platform=None, utc_now=None):
        self.config = copy.deepcopy(config)
        self.options = self.config['runtime']
        if self.options['kind'] != 'pipeline':
            raise ValueError('runtime kind must be pipeline')
        for name in ('ready_timeout_seconds','drain_timeout_seconds','poll_interval_seconds'):
            _positive(self.options[name], name)
        self.binaries = self.options['binaries']
        for name in ('compiler','numactl','dada_db','dada_dbnull','gpu','unpack','receiver'):
            self._binary(name)
        if platform is None:
            self._binary('gpu_probe')
        env = self.options['environment']
        if type(env) is not dict or any(type(k) is not str or type(v) is not str for k,v in env.items()):
            raise ValueError('explicit environment required')
        if 'CUDA_VISIBLE_DEVICES' in env or 'CUDA_DEVICE_ORDER' in env:
            raise ValueError('CUDA ordinal remapping is forbidden')
        ipc = self.options['ipc']
        if (set(ipc) != {'max_readers','header_buffers','exclusive_create'} or
                any(type(ipc[k]) is not int or not 1 <= ipc[k] <= 64 for k in ('max_readers','header_buffers')) or
                ipc['exclusive_create'] is not True):
            raise ValueError('verified exclusive PSRDADA build contract required')
        self.supervisor = supervisor
        self.journal = journal
        self.journal_thread = threading.get_ident()
        self.platform = platform if platform is not None else LinuxPlatform(self.binaries)
        self.utc_now = utc_now or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.sessions = {}
        self.lock = threading.Lock()
        self.closed = False
        self.wakeup = threading.Event()
        self.threads = {}

    def _binary(self, name):
        binary = self.binaries[name]
        path = _safe_path(binary['path'])
        if (set(binary) != {'path','sha256','leaf'} or not Path(binary['path']).is_absolute() or
                type(binary['leaf']) is not bool or not path.is_file() or
                not os.access(str(path),os.X_OK) or _sha_file(path) != binary['sha256']):
            raise ValueError('trusted binary changed: '+name)
        return str(path)

    def _now(self):
        now = self.utc_now()
        if not isinstance(now,datetime.datetime) or now.utcoffset() != datetime.timedelta(0):
            raise ValueError('UTC clock required')
        return now

    @staticmethod
    def _key(owner):
        return hashlib.sha256(_canonical(_owner(owner))).hexdigest()

    def _instance(self, owner):
        matches = [i for i in self.config['instances'] if all(i.get(k)==v for k,v in owner.items() if k!='generation')]
        if len(matches)!=1:
            raise ValueError('trusted instance/owner binding mismatch')
        return matches[0]

    def _actions(self, owner, operation, arguments):
        if self.journal is None:
            return []
        if threading.get_ident()!=self.journal_thread:
            raise ValueError('journal access must stay on its owning thread')
        record = self.journal.load(owner)
        if record['released'] or record['resources'] != _resources(self._instance(owner)['resources']):
            raise ValueError('claim changed/released')
        return [key for key, action in record['actions'].items()
                if action['outcome']=='UNKNOWN' and action['command']['operation']==operation and
                all(action['command'].get(k)==v for k,v in arguments.items())]

    def _new(self, owner):
        return dict(owner=copy.deepcopy(owner), lock=threading.RLock(), sequence=1,
                    state='NEW', cleanup='UNKNOWN', evidence=dict.fromkeys(FACTS,False), error=None,
                    actions={}, processes={}, rings=[], allocation_attempted=None,
                    allocation_uncertain=True, start=None,
                    cleanup_evidence=None, forced=False, directory=None, effect_uncertain=False,
                    compiler_pending=False)

    def _session(self, owner):
        key=self._key(owner)
        with self.lock:
            return self.sessions.get(key)

    def _snapshot(self, session, outcome=None):
        result={k:copy.deepcopy(session[k]) for k in ('sequence','state','cleanup','evidence','error')}
        if session['actions']:
            result['action_outcomes']=copy.deepcopy(session['actions'])
        if session['cleanup_evidence'] is not None:
            result['cleanup_evidence']=copy.deepcopy(session['cleanup_evidence'])
        if outcome is not None:
            result['outcome']=outcome
        return result

    @staticmethod
    def _ids(session, role):
        ids=dict(session['ids']);rings=session['ring_plan']
        ids['input_key']=int(rings['compute' if role=='gpu' else 'raw']['key'],16)
        if role!='receiver':
            ids['output_key']=int(rings['output' if role=='gpu' else 'compute']['key'],16)
        return ids

    def _update(self, session, **facts):
        if any(session.get(k)!=v for k,v in facts.items()):
            session.update(facts)
            session['sequence']+=1

    def _binding(self, owner, bundle_path):
        instance=self._instance(owner)
        root=_safe_path(bundle_path)
        if str(root)!=instance['bundle_path']:
            raise ValueError('installed bundle path mismatch')
        hashes=_manifest(root)
        bundle=_read_json(root/'bundle.json')
        identity=dict(bundle); identity.pop('bundle_id',None)
        if bundle['bundle_id']!=owner['bundle_id'] or hashlib.sha256(_canonical(identity)).hexdigest()!=owner['bundle_id']:
            raise ValueError('bundle identity mismatch')
        global_doc=validate_global(_read_json(root/'global-observation.json'))
        deployments=_read_json(root/'deployments.json')
        inventory=_read_json(root/'inventory.json')
        checked=validate_deployment(global_doc,deployments,inventory)
        for name,sha in bundle['inputs'].items():
            if hashes.get(name)!=sha:
                raise ValueError('bundle input identity mismatch')
        selected=[i for i in bundle['instances'] if i['server_id']==owner['server_id'] and i['gpu_id']==owner['gpu_id']]
        if len(selected)!=1:
            raise ValueError('bundle instance mismatch')
        selected=selected[0]
        profile=next(p for p in checked['instances'] if p['server_id']==owner['server_id'] and p['gpu_id']==owner['gpu_id'])
        if selected['deployment']!=profile or selected['observation_sha256']!=owner['observation_sha256']:
            raise ValueError('deployment/observation identity mismatch')
        directory=_safe_path(root/selected['path'])
        if hashes.get(selected['path']+'/observation.json')!=owner['observation_sha256']:
            raise ValueError('observation byte mismatch')
        resources=_resources(instance['resources'])
        host=next(h for h in inventory['hosts'] if profile['server_id'] in h['aliases'])
        cpus=[dict(logical_cpu=n, socket_id=host['cpus'][str(n)]['socket_id'],core_id=host['cpus'][str(n)]['core_id'])
              for n in sorted({c for values in profile['cpu'].values() for c in values})]
        expected=dict(gpu_uuid=profile['gpu_uuid'],cpus=cpus,ipc_keys=resources['ipc_keys'],
                      flow={k:profile['flow'][k] for k in ('physical_port','match')},
                      run_dir=str(_safe_path(Path(checked['feature_root'])/profile['paths']['run_dir'])))
        if resources!=_resources(expected):
            raise ValueError('trusted resource/deployment mismatch')
        return instance,profile,inventory,global_doc,directory,hashes

    def prepare(self, owner, bundle_path):
        owner=_owner(owner)
        actions=self._actions(owner,'prepare',{})
        key=self._key(owner)
        with self.lock:
            if self.closed:
                raise ValueError('runtime closed')
            if key in self.sessions:
                with self.sessions[key]['lock']:
                    return self._snapshot(self.sessions[key],'REJECTED')
            session=self._new(owner); self.sessions[key]=session
            thread=threading.Thread(target=self._schedule,args=(session,),
                                    name='pipeline-'+key[:12],daemon=True)
            self.threads[key]=thread
            thread.start()
        with session['lock']:
            self._update(session,state='PREPARING',cleanup='PENDING')
            try:
                # The claim is a declaration, independent of whether bundle
                # validation will succeed. Keep its exact normalized resources
                # available even when validation fails before any allocation.
                instance=self._instance(owner)
                session['resources']=_resources(instance['resources'])
                declared_directory=_safe_path(session['resources']['run_dir'])
                if declared_directory.exists():
                    raise ValueError('pre-existing run directory requires reconciliation')
                # Only this fresh invocation has positive no-attempt history.
                # _new alone (including observe/stop after restart) does not.
                session.update(allocation_attempted=False,allocation_uncertain=False)
                for name in self.binaries:
                    self._binary(name)
                instance,profile,inventory,global_doc,installed,hashes=self._binding(owner,bundle_path)
                session.update(resources=_resources(instance['resources']),profile=profile,global_doc=global_doc,
                               bundle_path=bundle_path,bundle_hashes=hashes)
                if (len(profile['cpu']['unpack'])!=2 or
                        len(profile['cpu']['workers'])!=profile['execution']['worker_count'] or
                        profile['numa']['unpack']!=profile['numa']['workers']):
                    raise ValueError('unpack needs coordinator/writer and exact worker CPUs on one NUMA node')
                self.platform.check_host(profile,inventory)
                directory=_safe_path(session['resources']['run_dir'])
                # A pre-existing directory may carry an unresolved generation.
                directory.parent.mkdir(parents=True,exist_ok=True)
                try:
                    directory.mkdir(mode=0o700)
                except FileExistsError:
                    # Another preparer may have won after the absence check.
                    session.update(allocation_attempted=None,allocation_uncertain=True)
                    raise
                session['directory']=directory
                (directory/'owner.json').write_bytes(_canonical(owner))
                resolution=resolve_instance(installed,Path(self._binary('compiler')),directory/'compiled',
                    run_compiler=lambda argv:self._run_compiler(session,argv,self.options['ready_timeout_seconds']))
                if _manifest(Path(bundle_path))!=hashes:
                    raise ValueError('bundle changed during compiler resolution')
                session['ids']={k:resolution['report'][k] for k in ('config_id','geometry_id')}
                session['derived']=resolution['report']['derived']
                if type(session['derived']['expected_groups']) is not int or session['derived']['expected_groups']<=0:
                    raise ValueError('compiler target group count invalid')
                rings=_read_json(directory/'compiled/ring_plan.json')['rings']
                if set(rings)!={'raw','compute','output'}:
                    raise ValueError('three compiler rings required')
                footprint=[]
                for role,ring in rings.items():
                    if (int(ring['key'],16)!=int(profile['rings'][role+'_key'],16) or
                            ring['blocks']!=profile['ring_counts'][role] or
                            any(type(ring[k]) is not int or ring[k]<=0 for k in ('blocks','block_bytes','header_bytes'))):
                        raise ValueError('compiler ring/deployment mismatch')
                    footprint+=ring_footprint(ring,self.options['ipc'])
                pairs=[(i['kind'],i['key']) for i in footprint]
                if len(pairs)!=len(set(pairs)) or sorted({i['key'] for i in footprint})!=session['resources']['ipc_keys']:
                    raise ValueError('expanded IPC claim mismatch/collision')
                self.platform.ensure_absent(footprint)
                session['allocation_uncertain']=False
                session['ring_plan']=rings
                for role in ('raw','compute','output'):
                    ring=rings[role]
                    with (directory/('ring-'+role+'.intent')).open('xb') as stream:
                        stream.write(_canonical(dict(owner=owner,ring=ring,
                            footprint=ring_footprint(ring,self.options['ipc']))))
                        stream.flush();os.fsync(stream.fileno())
                    argv=[self._binary('dada_db'),'-k',format(int(ring['key'],16),'x'),'-b',str(ring['block_bytes']),
                          '-n',str(ring['blocks']),'-a',str(ring['header_bytes']),'-r','1','-c',str(profile['numa']['receiver' if role=='raw' else 'gpu'])]
                    session['allocation_uncertain']=True
                    report=self._launch(session,'ring-'+role,argv,None)
                    report=self._wait_exit(report,self.options['ready_timeout_seconds'])
                    session['processes']['ring-'+role]=report
                    token=self.platform.capture_ring(ring,report,ring_footprint(ring,self.options['ipc']))
                    session['rings'].append(token)
                    with (directory/('ring-'+role+'.objects')).open('xb') as stream:
                        stream.write(_canonical(token));stream.flush();os.fsync(stream.fileno())
                    session['allocation_uncertain']=False
                plan=str(directory/'compiled/resolved_observation.json')
                for role in ROLES:
                    if role=='sink':
                        argv=[self._binary('dada_dbnull'),'-k',format(int(rings['output']['key'],16),'x'),'-s','-z']
                    elif role=='gpu':
                        argv=[self._binary('gpu'),plan,'--metrics-json',str(directory/'gpu.metrics'),
                              '--ready-file',str(directory/'gpu.ready'),'--result-json',str(directory/'gpu.result')]
                    else:
                        argv=[self._binary(role),'--plan',plan,'--ready-file',str(directory/(role+'.ready')),
                              '--result-json',str(directory/(role+'.result'))]
                        if role=='unpack':
                            cpus=profile['cpu']['unpack'][:1]+profile['cpu']['workers']+profile['cpu']['unpack'][1:]
                            # Engine needs coordinator + parse/copy workers + writer.
                            if len(profile['cpu']['unpack'])!=2 or len(profile['cpu']['workers'])!=profile['execution']['worker_count']:
                                raise ValueError('unpack needs an explicit coordinator/workers/writer CPU list')
                            argv+=['--thread-cpus',','.join(str(c) for c in cpus)]
                        else:
                            argv+=['--recv-wr-num',str(profile['execution']['receiver_wr_num']),
                                   '--poll-batch',str(profile['execution']['receiver_poll_batch']),
                                   '--poll-cpu',str(profile['cpu']['receiver'][0])]
                    report=self._launch(session,role,argv,role)
                    limit=time.monotonic()+self.options['ready_timeout_seconds']
                    while True:
                        report=self.supervisor.inspect(report['identity']);session['processes'][role]=report
                        ring=session['rings'][2] if role=='sink' else rings['raw' if role in ('unpack','receiver') else 'compute']
                        if report['state']=='RUNNING' and self.platform.ready(role,report,directory,self._ids(session,role),ring):
                            break
                        if report['state']!='RUNNING' or time.monotonic()>=limit:
                            raise ValueError('READY_TIMEOUT:'+role)
                        time.sleep(self.options['poll_interval_seconds'])
                self._inspect(session)
                if any(session['processes'][role]['state']!='RUNNING' for role in ROLES):
                    raise ValueError('consumer lost readiness during preparation')
                self._update(session,state='READY')
                outcome='APPLIED'
            except Exception as error:
                self._update(session,error=str(error) or type(error).__name__)
                self._drain(session,'FAILED',self.options['drain_timeout_seconds'],force=False)
                outcome=('UNKNOWN' if session['effect_uncertain'] or session['allocation_uncertain'] or
                         session['compiler_pending'] or
                         session['allocation_attempted'] is None else
                         'NOT_APPLIED' if session['allocation_attempted'] is False else 'APPLIED')
            session['actions'].update({a:outcome for a in actions})
            return self._snapshot(session,outcome)

    def _launch(self, session, role, argv, placement):
        if placement is not None:
            profile=session['profile']
            cpus=profile['cpu'][placement]
            if placement=='unpack':
                cpus=cpus+profile['cpu']['workers']
            argv=[self._binary('numactl'),'--physcpubind='+','.join(str(c) for c in cpus),
                  '--membind='+str(profile['numa'][placement])]+argv
        session.update(allocation_attempted=True,effect_uncertain=True)
        report=self.supervisor.launch(session['owner'],role,argv,str(session['directory']),self.options['environment'])
        session['processes'][role]=report
        session['effect_uncertain']=report['state']=='UNKNOWN'
        return report

    @staticmethod
    def _compiler_done(report):
        return (report['state']=='NOT_STARTED' or
                (report['state']=='EXITED' and type(report['returncode']) is int))

    @staticmethod
    def _compiler_output(report):
        output={};truncated={}
        for name in ('stdout','stderr'):
            path=_safe_path(report['logs'][name])
            fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                _private(stream.fileno())
                raw=stream.read(COMPILER_OUTPUT_LIMIT+1)
            truncated[name]=len(raw)>COMPILER_OUTPUT_LIMIT
            output[name]=raw[:COMPILER_OUTPUT_LIMIT].decode('utf-8',errors='replace')
        return output,truncated

    def _run_compiler(self, session, argv, timeout_seconds):
        """One no-fork compiler, on the SAME retained B2 supervisor, no PIPEs.

        The deadline includes launch. Unknown identity never authorizes a kill;
        a bounded reap window retains unresolved children/claims explicitly.
        """
        deadline=time.monotonic()+timeout_seconds
        session['compiler_pending']=True  # Also covers launch failure after a possible spawn.
        try:
            report=self.supervisor.launch(session['owner'],'compiler',argv,str(Path(argv[-1]).parent),
                                          self.options['environment'])
        except ProcessError as error:
            raise ValueError('COMPILER_SUPERVISION: '+str(error)) from error
        session['processes']['compiler']=report
        while not self._compiler_done(report) and time.monotonic()<deadline:
            report=self.supervisor.wait(report['identity'],0)
            session['processes']['compiler']=report
            if not self._compiler_done(report):
                time.sleep(min(self.options['poll_interval_seconds'],max(0,deadline-time.monotonic())))
        timed_out=not self._compiler_done(report)
        if timed_out:
            reap_deadline=time.monotonic()+min(timeout_seconds,1)
            while True:
                self._signal(session,'compiler',signal.SIGKILL)
                report=self.supervisor.wait(report['identity'],0)
                session['processes']['compiler']=report
                if self._compiler_done(report) or time.monotonic()>=reap_deadline:
                    break
                time.sleep(min(self.options['poll_interval_seconds'],max(0,reap_deadline-time.monotonic())))
        session['compiler_pending']=not self._compiler_done(report)
        output=dict(stdout='',stderr='');truncated=dict(stdout=False,stderr=False)
        log_error=None
        try:
            output,truncated=self._compiler_output(report)
        except Exception as error:
            if not timed_out:
                failure=ValueError('COMPILER_LOG: '+str(error))
                failure.compiler_process=copy.deepcopy(report)
                raise failure from error
            log_error=str(error) or type(error).__name__  # Never replace the original deadline failure.
        if timed_out:
            error=subprocess.TimeoutExpired(argv,timeout_seconds,output=output['stdout'],stderr=output['stderr'])
            error.compiler_process=copy.deepcopy(report)
            error.output_truncated=truncated
            if log_error is not None:
                error.compiler_log_error=log_error
            raise error
        if report['state']=='NOT_STARTED':
            error=OSError('compiler spawn failed')
            error.compiler_process=copy.deepcopy(report)
            raise error
        completed=subprocess.CompletedProcess(argv,report['returncode'],output['stdout'],output['stderr'])
        completed.compiler_process=copy.deepcopy(report)
        completed.output_truncated=truncated
        return completed

    def start(self, owner, utc_start, duration_seconds):
        owner=_owner(owner)
        actions=self._actions(owner,'start',dict(utc_start=utc_start,duration_seconds=duration_seconds))
        session=self._session(owner)
        if session is None:
            return self._snapshot(self._new(owner),'REJECTED')
        with session['lock']:
            if session['state']!='READY':
                return self._snapshot(session,'REJECTED')
            observation=session['global_doc']['observation']
            try:
                start=_utc(utc_start)
                duration=Decimal(str(duration_seconds))
                if _manifest(Path(session['bundle_path']))!=session['bundle_hashes']:
                    raise ValueError('installed bundle changed after preparation')
                if (type(duration_seconds) not in (int,float,str) or
                        utc_start!=observation['utc_start'] or duration_seconds!=observation['duration_seconds'] or
                        not duration.is_finite() or duration<=0 or self._now()>start or session['state']!='READY'):
                    raise ValueError('original future UTC/duration and READY required')
                if any(self.supervisor.inspect(r['identity'])['state']!='RUNNING'
                       for role,r in session['processes'].items() if role in ROLES):
                    raise ValueError('readiness lost before arming')
                session['start']=(utc_start,duration_seconds,start,duration)
                self._update(session,state='STARTING')
                outcome='APPLIED'
                self.wakeup.set()
            except (ValueError,TypeError,ArithmeticError) as error:
                outcome='REJECTED'
                if session['state']=='READY':
                    self._update(session,error=str(error) or 'start rejected')
                    self._drain(session,'FAILED',self.options['drain_timeout_seconds'])
            session['actions'].update({a:outcome for a in actions})
            return self._snapshot(session,outcome)

    def _inspect(self, session):
        for role,report in list(session['processes'].items()):
            session['processes'][role]=self.supervisor.inspect(report['identity'])

    def _wait_exit(self, report, timeout):
        # B2 wait holds its host-wide mutex. Poll with zero wait so another
        # owner's deadline can still signal ingress while this child is slow.
        deadline=time.monotonic()+timeout
        while True:
            report=self.supervisor.wait(report['identity'],0)
            if report['state']!='RUNNING' or time.monotonic()>=deadline:
                return report
            time.sleep(min(self.options['poll_interval_seconds'],max(0,deadline-time.monotonic())))

    def _signal(self, session, role, signum):
        report=session['processes'].get(role)
        if report is not None:
            report=self.supervisor.inspect(report['identity'])
            session['processes'][role]=report
            if report['state']=='RUNNING':
                # B2 additionally requires the retained Popen handle, so this
                # can never signal an adopted/nonchild PID after restart.
                self.supervisor.signal(report['identity'],signum)

    def _completion(self, session):
        records={role:self.platform.result(role,session['processes'][role],session['directory'],self._ids(session,role))
                 for role in ('receiver','unpack','gpu') if role in session['processes']}
        if any(not records.get(role) for role in ('receiver','unpack','gpu')):
            return dict.fromkeys(FACTS,False)
        receiver,unpack,gpu=(records[r] for r in ('receiver','unpack','gpu'))
        observation=session['global_doc']['observation']
        groups=session['derived']['expected_groups']
        samples=groups*session['global_doc']['wire']['samples_per_packet']
        block_samples=session['global_doc']['time_grid']['samples_per_block']
        rings=session['ring_plan']
        input_bytes=rings['compute']['block_bytes']*samples//block_samples
        output_bytes=rings['output']['block_bytes']*samples//block_samples
        blocks=(samples+block_samples-1)//block_samples
        station=unpack.get('station_accepted_packets')
        highest=unpack.get('station_highest_ordinals')
        data=(unpack.get('expected_groups')==groups and unpack.get('station_ids')==observation['station_ids'] and
              type(station) is list and len(station)==len(observation['station_ids']) and
              all(type(n) is int and 0<n<=groups for n in station) and
              type(highest) is list and len(highest)==len(station) and
              all(type(n) is int and n==groups-1 for n in highest) and
              unpack.get('emitted_bytes')==input_bytes and gpu.get('input_bytes')==input_bytes and
              type(receiver.get('committed_bytes')) is int and receiver['committed_bytes']>0)
        output=(gpu.get('output_bytes')==output_bytes and type(gpu.get('published_blocks')) is int and
                gpu['published_blocks']==blocks)
        clean=all(record.get('ok') is True for record in records.values())
        eod=(clean and receiver.get('eod') is True and unpack.get('input_eod') is True and
             unpack.get('eod') is True and gpu.get('input_eod') is True and gpu.get('output_eod') is True)
        return dict(target_data_complete=bool(data and clean),output_published=bool(output and clean),eod_complete=bool(eod))

    def _cleanup(self, session):
        if session['allocation_attempted'] is False:
            # This is explicit current-invocation history, not an inference
            # from an empty process table or a stale journal claim. No pipeline
            # worker/allocator was attempted. Any compiler must additionally
            # have an authoritative reap and reviewed no-fork proof; having
            # created no ring alone is insufficient. Foreign IPC stays intact.
            self._inspect(session)
            compiler=session['processes'].get('compiler')
            if compiler is not None:
                session['compiler_pending']=not self._compiler_done(compiler)
            compiler_gone=(compiler is None or (self._compiler_done(compiler) and
                (compiler['state']=='NOT_STARTED' or self.binaries['compiler']['leaf'] is True)))
            unallocated=(not (set(session['processes'])-{'compiler'}) and compiler_gone and
                         not session['compiler_pending'] and not session['rings'] and
                         not session['effect_uncertain'] and not session['allocation_uncertain'])
            if unallocated and 'resources' in session:
                evidence=dict(owner=session['owner'],resources=session['resources'],processes_gone=True,
                              descendants_gone=True,resources_gone=True)
                self._update(session,cleanup='CONFIRMED',cleanup_evidence=evidence)
            else:
                self._update(session,cleanup='UNKNOWN',cleanup_evidence=None)
            return
        self._inspect(session)
        reports=list(session['processes'].values())
        gone=all(r['state'] in ('EXITED','NOT_STARTED') and
                 (type(r['returncode']) is int or r['state']=='NOT_STARTED') for r in reports)
        descendants=gone and self.platform.descendants_gone(reports,self.binaries)
        resources=not session['allocation_uncertain'] and not session['effect_uncertain']
        if gone and descendants:
            for token in session['rings']:
                if not self.platform.remove_ring(token):
                    resources=False
        else:
            resources=False
        if gone and descendants and resources and 'resources' in session:
            evidence=dict(owner=session['owner'],resources=session['resources'],processes_gone=True,
                          descendants_gone=True,resources_gone=True)
            self._update(session,cleanup='CONFIRMED',cleanup_evidence=evidence)
        else:
            self._update(session,cleanup='UNKNOWN',cleanup_evidence=None)

    def _drain(self, session, terminal, timeout, force=False):
        self._update(session,state='DRAINING')
        self._signal(session,'receiver',signal.SIGTERM)
        deadline=time.monotonic()+(0 if force else timeout)
        while True:
            self._inspect(session)
            active=[r for r in session['processes'].values() if r['state']=='RUNNING']
            if not active or time.monotonic()>=deadline:
                break
            time.sleep(min(self.options['poll_interval_seconds'],max(0,deadline-time.monotonic())))
        # Rollback before receiver exists cannot propagate EOD. Force only the
        # exact retained roles; UNKNOWN remains unkillable and unclean.
        for role,report in session['processes'].items():
            if report['state']=='RUNNING':
                session['forced']=True
                self._signal(session,role,signal.SIGKILL)
        reap_deadline=time.monotonic()+min(timeout,1)
        for role,report in list(session['processes'].items()):
            session['processes'][role]=self._wait_exit(report,max(0,reap_deadline-time.monotonic()))
        evidence=dict.fromkeys(FACTS,False)
        if not session['forced'] and 'ids' in session and all(r['state']=='EXITED' and r['returncode']==0 for r in session['processes'].values()):
            evidence=self._completion(session)
        if terminal=='COMPLETED' and not all(evidence.values()):
            terminal='FAILED'
            self._update(session,error=session['error'] or 'incomplete data/output/EOD or forced drain')
        self._update(session,state=terminal,evidence=evidence)
        self._cleanup(session)

    def stop(self, owner, mode, timeout_seconds):
        owner=_owner(owner)
        _positive(timeout_seconds,'stop timeout')
        if mode not in ('graceful','force'):
            raise ValueError('invalid stop mode')
        actions=self._actions(owner,'stop',dict(mode=mode))
        session=self._session(owner)
        if session is None:
            return self._snapshot(self._new(owner),'UNKNOWN')
        with session['lock']:
            if session['state'] not in TERMINAL:
                self._drain(session,'INTERRUPTED',timeout_seconds,force=mode=='force')
            elif session['cleanup']!='CONFIRMED':
                # Retain terminal history while retrying bounded owned cleanup.
                state=session['state'];evidence=copy.deepcopy(session['evidence'])
                self._drain(session,state,timeout_seconds,force=mode=='force')
                self._update(session,state=state,evidence=evidence)
            session['actions'].update({a:'APPLIED' for a in actions})
            return self._snapshot(session,'APPLIED')

    def observe(self, owner):
        owner=_owner(owner)
        session=self._session(owner)
        if session is None:
            return self._snapshot(self._new(owner))
        with session['lock']:
            trustworthy=self._advance(session)
            if trustworthy and session['state'] not in TERMINAL:
                session['sequence']+=1  # One trustworthy sample, not a cached heartbeat.
            return self._snapshot(session)

    def _advance(self, session):
        if session['state'] in TERMINAL or session['state'] in ('NEW','PREPARING','DRAINING'):
            return False
        self._inspect(session)
        workers={role:session['processes'][role] for role in ROLES if role in session['processes']}
        if any(r['state']=='EXITED' and type(r['returncode']) is int and r['returncode']!=0
               for r in workers.values()):
            self._update(session,error='worker failure')
            self._drain(session,'FAILED',self.options['drain_timeout_seconds'])
            return False
        unknown=any(r['state']=='UNKNOWN' for r in workers.values())
        schedule=session['start']
        if schedule is not None:
            now=self._now()
            delta=now-schedule[2]
            elapsed=Decimal(delta.days*86400+delta.seconds)+Decimal(delta.microseconds)/1000000
            if elapsed>=schedule[3]:
                self._drain(session,'COMPLETED',self.options['drain_timeout_seconds'])
                return False
            if unknown:
                return False  # Preserve facts/sequence; retained-child reaping may resolve this later.
            if now>=schedule[2]:
                self._update(session,state='RUNNING')
        elif unknown:
            return False
        elif any(r['state']!='RUNNING' for r in workers.values()):
            self._update(session,error='worker exited before authorized start')
            self._drain(session,'FAILED',self.options['drain_timeout_seconds'])
            return False
        return True

    def _schedule(self, session):
        while not self.closed:
            self.wakeup.wait(self.options['poll_interval_seconds']);self.wakeup.clear()
            if not session['lock'].acquire(blocking=False):
                continue
            try:
                self._advance(session)
            except Exception as error:
                self._update(session,error=str(error) or type(error).__name__)
                try:
                    self._drain(session,'FAILED',self.options['drain_timeout_seconds'])
                except Exception:
                    self._update(session,state='FAILED',cleanup='UNKNOWN',cleanup_evidence=None)
            finally:
                session['lock'].release()

    def close(self):
        """Bounded shutdown of this runtime's owned children; never a remote stop."""
        self.closed=True;self.wakeup.set()
        deadline=time.monotonic()+self.options['drain_timeout_seconds']+2
        for thread in self.threads.values():
            thread.join(timeout=max(0,deadline-time.monotonic()))
        if any(thread.is_alive() for thread in self.threads.values()):
            raise RuntimeError('scheduler shutdown unresolved; retain supervisor handles')
        for session in list(self.sessions.values()):
            with session['lock']:
                if session['state'] not in TERMINAL:
                    self._drain(session,'INTERRUPTED',self.options['drain_timeout_seconds'],force=True)
