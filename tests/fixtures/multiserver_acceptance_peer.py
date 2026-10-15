#!/usr/bin/env python3
"""Fixed MS07 acceptance peer; trusted config, no arbitrary client argv/paths.

HF invokes this entrypoint via SSHHostAdapter and explicit --deployment. Sender
role config: schema_version=1,role='sender',server_id,root,identity,
lifetime_seconds,binary={path,sha256,leaf:true},jobs. Each job has run_id,
bundle_id,server_id (receiver),gpu_id,observation_sha256,config_path,config_sha256.
Only SHA-pinned schema2/3 PACED fpga_sender_sim CONFIG is launched. No numactl.

Receiver role config: schema_version=1,role='receiver',server_id,root,identity,
host_deployment_path,host_deployment_sha256. It reads production ownership,
process/IPC ledgers and machine-readable product results. The only receiver
mutation is the fixed operator --retire CLI at campaign end; no PID signalling.
The same module exposes a fixed FaultPipelineRuntime for the development-only
host factory. runtime.fault is a nonempty list of up to three target
objects {run_id,server_id,gpu_id,after_running_seconds,role:'gpu'} so all three
repetitions keep one immutable deployment. Non-target runs follow normal C1.
check/collect project that pinned declaration as evidence only. collect reads
fault.json and validates its owner, generation and retained GPU fingerprint
against the authoritative process ledger; it never executes those fields.

Sender private per-run mailbox intent precedes detached daemon launch. A single
retained B2 supervisor owns all sender Popen handles until authoritative reaps.
The finite lifetime enforces stop; request exit never kills the child. No daemon
restart/adoption or process-name/key cleanup. Existing results are never deleted.
Health queries are read-only; a nonzero sender exit also causes the daemon to
stop and reap its remaining retained children immediately. inject is a read-only
sender no-op; only the receiver's startup deployment can arm a GPU fault.
prepare_rollback uses the fixed private <bundle_path>.acceptance-negative.json
declaration verified by negative_fixture(), never an in-place source mutation.
All configs, roots and binaries must be private/operator trusted and installed
before dispatch. SHA/leaf asserts a reviewed no-fork simulator build, not proof
that an arbitrary executable is safe. Linux /proc is required for actual launch.
"""
import argparse
import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from multiserver.host_agent import PrivateRoot, instance_key
from multiserver.ownership import _json as journal_encode
from multiserver.processes import ProcessSupervisor, _private, _read_process
from multiserver.pipeline_runtime import LinuxPlatform, PipelineRuntime
from multiserver.pipeline_runtime import _manifest
from multiserver.bundle import _safe_path
from multiserver.transport import _decode, _encode, _LIMIT
from multiserver_host import load_deployment, read_stdin


class FaultPipelineRuntime(PipelineRuntime):
    """Fixed trusted gpu-exit fault, constructed only by development factory."""
    def __init__(self,config,supervisor,**kwargs):
        import copy
        config=copy.deepcopy(config)
        options=config['runtime']
        if options['kind']!='pipeline-fixture':raise ValueError('development runtime kind required')
        faults=options.pop('fault')
        if type(faults) is not list or not 1<=len(faults)<=3:
            raise ValueError('one to three fixed fault targets required')
        targets=set()
        for fault in faults:
            if (type(fault) is not dict or set(fault)!={'run_id','server_id','gpu_id','after_running_seconds','role'} or
                    fault['role']!='gpu' or any(type(fault[k]) is not str or not fault[k] for k in ('run_id','server_id')) or type(fault['gpu_id']) is not int or fault['gpu_id']<0 or
                    type(fault['after_running_seconds']) not in (int,float) or
                    not 0<=fault['after_running_seconds']<3600):
                raise ValueError('invalid fixed GPU fault')
            key=(fault['run_id'],fault['server_id'],fault['gpu_id'])
            if key in targets:raise ValueError('duplicate fault target')
            targets.add(key)
            matches=[i for i in config['instances'] if all(i[k]==fault[k] for k in ('run_id','server_id','gpu_id'))]
            if len(matches)!=1:raise ValueError('fault must select one installed binding')
            document=read(Path(matches[0]['bundle_path'])/'global-observation.json')
            duration=Decimal(str(document['observation']['duration_seconds']))
            if not duration.is_finite() or Decimal(str(fault['after_running_seconds']))>=duration:
                raise ValueError('fault delay must be within observation duration')
        self.faults=faults
        options['kind']='pipeline'
        super().__init__(config,supervisor,**kwargs)

    def _binding(self,owner,bundle_path):
        result=super()._binding(owner,bundle_path)
        fault=self._target(owner)
        if fault and Decimal(str(fault['after_running_seconds']))>=Decimal(str(result[3]['observation']['duration_seconds'])):
            raise ValueError('fault delay must be within observation duration')
        return result

    def _target(self,owner):
        return next((fault for fault in self.faults if all(owner[k]==fault[k] for k in ('run_id','server_id','gpu_id'))),None)

    def _advance(self,session):
        super()._advance(session)
        if session['state']!='RUNNING' or not self._target(session['owner']) or session.get('fault_injected'):
            return
        elapsed=self._now()-session['start'][2]
        if Decimal(str(elapsed.total_seconds()))<Decimal(str(self._target(session['owner'])['after_running_seconds'])):return
        session['fault_injected']=True
        report=session['processes']['gpu']
        # Parent _signal requires its retained, identity-matched B2 Popen handle.
        proof=dict(owner=session['owner'],identity=report['identity'],role='gpu',signal='SIGKILL')
        with (session['directory']/'fault.json').open('xb') as stream:
            stream.write(_encode(proof));stream.flush();os.fsync(stream.fileno())
        self._signal(session,'gpu',signal.SIGKILL)
        super()._advance(session)


def read(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:
            raise ValueError('unsafe fixture input')
        raw=stream.read(_LIMIT+1)
    if len(raw)>_LIMIT:raise ValueError('fixture record too large')
    return _decode(raw)


def sha(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('regular input required')
        digest=hashlib.sha256()
        for block in iter(lambda:stream.read(65536),b''):digest.update(block)
    return digest.hexdigest()


def pin(path,digest):
    if type(path) is not str or not os.path.isabs(path) or sha(path)!=digest:
        raise ValueError('pinned fixture bytes changed')


def negative_fixture(installed,context,healthy_paths):
    """Verify the sole predeclared byte deviation in an independent test copy.

    The private <bundle_path>.acceptance-negative.json is operator-installed,
    outside the unchanged bundle manifest. It is identity evidence, never an
    instruction to modify a file or select an executable.
    """
    try:
        return _negative_fixture(installed,context,healthy_paths)
    except OSError as error:
        # Missing/unsafe negative evidence is a harness identity defect, not
        # permission to treat an accidental source-sync failure as this case.
        raise ValueError('negative fixture identity is missing or unsafe') from error


def _negative_fixture(installed,context,healthy_paths):
    root=_safe_path(installed['bundle_path'])
    sidecar=Path(str(root)+'.acceptance-negative.json')
    fd=os.open(str(sidecar),os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:_private(fd)
    finally:os.close(fd)
    declaration=read(sidecar)
    fields={'schema_version','run_id','bundle_id','server_id','gpu_id','expected_error',
            'baseline_manifest_sha256','relative_path','original_sha256','negative_sha256'}
    if set(declaration)!=fields or type(declaration['schema_version']) is not int or declaration['schema_version']!=1:
        raise ValueError('invalid fixed negative fixture declaration')
    if (type(declaration['gpu_id']) is not int or
            any(declaration[k]!=installed[k] for k in ('run_id','bundle_id','server_id','gpu_id')) or
            declaration['bundle_id']!=context['bundle_id'] or
            any(declaration[k]!=context['prepare_failure'][k] for k in ('server_id','gpu_id','expected_error')) or
            declaration['baseline_manifest_sha256']!=context['bundle_manifest_sha256'] or
            any(type(declaration[k]) is not str or not re.fullmatch('[0-9a-f]{64}',declaration[k])
                for k in ('original_sha256','negative_sha256','baseline_manifest_sha256'))):
        raise ValueError('negative fixture binding/identity mismatch')
    relative=declaration['relative_path']
    if (type(relative) is not str or not relative or Path(relative).is_absolute() or
            Path(relative).as_posix()!=relative or any(p in ('.','..') for p in Path(relative).parts)):
        raise ValueError('negative fixture path must be a canonical relative file')
    changed=_safe_path(root/relative)
    if not changed.is_file() or root not in changed.parents or changed.stat().st_nlink!=1:
        raise ValueError('negative fixture modified file is shared or unsafe')
    info=changed.stat()
    for path in healthy_paths:
        healthy=_safe_path(path)
        if root==healthy or root in healthy.parents or healthy in root.parents:
            raise ValueError('negative bundle must be independent of healthy bundles')
        counterpart=healthy/relative
        if counterpart.exists():
            other=counterpart.stat()
            if (info.st_dev,info.st_ino)==(other.st_dev,other.st_ino):
                raise ValueError('modified file is shared with a healthy bundle')
    manifest=root/'MANIFEST.sha256'
    if sha(manifest)!=context['bundle_manifest_sha256']:
        raise ValueError('negative fixture changed the baseline manifest')
    expected={}
    for line in manifest.read_text(encoding='ascii').splitlines():
        digest,name=line.split('  ',1)
        if name in expected or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('invalid negative bundle baseline manifest')
        expected[name]=digest
    if (expected.get(relative)!=declaration['original_sha256'] or
            declaration['negative_sha256']==declaration['original_sha256']):
        raise ValueError('declared deviation does not match baseline bytes')
    for name,digest in expected.items():
        path=_safe_path(root/name)
        if root not in path.parents or not path.is_file():raise ValueError('unsafe negative bundle entry')
        wanted=declaration['negative_sha256'] if name==relative else digest
        if sha(path)!=wanted:raise ValueError('negative bundle has an undeclared byte deviation')
    actual=set()
    for path in root.rglob('*'):
        path=_safe_path(path)
        if path.is_file() and path!=manifest:actual.add(path.relative_to(root).as_posix())
    if actual!=set(expected):raise ValueError('negative bundle file set changed')
    return dict(binding={k:installed[k] for k in ('run_id','bundle_id','server_id','gpu_id')},
        expected_error=declaration['expected_error'],negative_root=str(root),relative_path=relative,
        original_sha256=declaration['original_sha256'],negative_sha256=declaration['negative_sha256'],
        baseline_manifest_sha256=declaration['baseline_manifest_sha256'],
        sidecar_path=str(sidecar),sidecar_sha256=sha(sidecar))


def validate_config(config):
    common={'schema_version','role','server_id','root','identity'}
    extra=({'binary','jobs','lifetime_seconds'} if config.get('role')=='sender' else
           {'host_deployment_path','host_deployment_sha256'})
    if set(config)!=common|extra or type(config['schema_version']) is not int or config['schema_version']!=1:
        raise ValueError('unknown fixed fixture configuration')
    if not os.path.isabs(config['root']) or config['role'] not in ('sender','receiver'):
        raise ValueError('invalid fixture role/root')
    if config['role']=='sender':
        binary=config['binary']
        if set(binary)!={'path','sha256','leaf'} or binary['leaf'] is not True:
            raise ValueError('reviewed no-fork sender contract required')
        pin(binary['path'],binary['sha256'])
        if Path(binary['path']).name!='fpga_sender_sim' or not os.access(binary['path'],os.X_OK):
            raise ValueError('fixed fpga_sender_sim executable required')
        if type(config['lifetime_seconds']) not in (int,float) or not 0<config['lifetime_seconds']<=3600:
            raise ValueError('finite sender lifetime required')
        if not isinstance(config['jobs'],list) or not config['jobs']:raise ValueError('sender jobs required')
    else:
        pin(config['host_deployment_path'],config['host_deployment_sha256'])
    return config


def check_sender(config,context):
    validate_config(config)
    observation=context['observation']
    assignments={(i['server_id'],i['gpu_id']):i for i in context['assignments']}
    jobs=[j for j in config['jobs'] if j['run_id']==context['run_id']]
    if not jobs:raise ValueError('no preinstalled sender jobs for run')
    result=[]
    for job in jobs:
        if set(job)!={'run_id','bundle_id','server_id','gpu_id','observation_sha256','config_path','config_sha256'}:
            raise ValueError('invalid trusted sender job')
        pin(job['config_path'],job['config_sha256'])
        cfg=read(job['config_path']);packet=cfg['packet'];schedule=cfg['time']
        assignment=assignments[(job['server_id'],job['gpu_id'])]
        group_count=Decimal(str(observation['duration_seconds']))*10**12/(
            observation['sample_interval_ps']*context['samples_per_packet'])
        stations=cfg['station']['station_ids'] if cfg.get('schema_version')==3 else [cfg['station']['station_id']]
        utc=datetime.datetime.strptime(observation['utc_start'],'%Y-%m-%d-%H:%M:%S')
        epoch_start=datetime.datetime(utc.year,1 if utc.month<=6 else 7,1)
        epoch=(utc.year-2000)*2+(utc.month>6)
        destination=next(i for i in context['destinations'] if i['server_id']==job['server_id'] and i['gpu_id']==job['gpu_id'])
        match=destination['match']
        if (job['bundle_id']!=context['bundle_id'] or cfg.get('schema_version') not in (2,3) or
                schedule.get('reference_epoch')!=epoch or schedule.get('start_seconds')!=int((utc-epoch_start).total_seconds()) or
                cfg['destination']['ip']!=match['destination_ip'] or cfg['destination']['port']!=match['destination_port'] or
                ('source_ip' in match and cfg['source']['ip']!=match['source_ip']) or
                ('source_port' in match and cfg['source']['port']!=match['source_port']) or schedule['mode']!='PACED' or schedule['start_utc']!=observation['utc_start'] or
                type(schedule['group_count']) is not int or schedule['group_count']!=group_count or
                packet['first_channel_id']!=assignment['first_channel_id'] or packet['nchan']!=assignment['nchan'] or
                packet['npol']!=observation['npol'] or packet['nsamp_per_packet']!=context['samples_per_packet'] or
                int(packet['sample_interval_ps'])!=observation['sample_interval_ps'] or packet['component_bits']!=8 or
                len(set(stations))!=len(stations) or not stations or not set(stations)<=set(observation['station_ids']) or
                any(cfg.get('faults',{}).values())):
            raise ValueError('sender allocation/schedule mismatch')
        record_bytes=packet['nsamp_per_packet']*packet['nchan']*packet['npol']*2+32
        minimum_bits=Decimal(record_bytes*schedule['group_count']*len(stations)*8)
        rate=Decimal(str(cfg['transmit']['target_gbps']))*10**9
        if not rate.is_finite() or rate<=0 or rate*Decimal(str(observation['duration_seconds']))<minimum_bits:
            raise ValueError('paced sender budget exceeds original data interval')
        result.append(dict(job=job,config=cfg,stations=stations,
                           argv=[config['binary']['path'],job['config_path']]))
    return result


def check(config,context):
    if config['identity']!=context['identity']:raise ValueError('fixture source/build identity mismatch')
    if config['role']=='sender':
        jobs=check_sender(config,context)
        return dict(supported=True,reason=None,coverage=[dict(server_id=j['job']['server_id'],
            gpu_id=j['job']['gpu_id'],station_ids=j['stations'],config_sha256=j['job']['config_sha256']) for j in jobs])
    host=load_deployment(config['host_deployment_path'])
    installed=[i for i in host['instances'] if i['run_id']==context['run_id']]
    if not installed or any(i['bundle_id']!=context['bundle_id'] for i in installed):
        raise ValueError('receiver installed binding mismatch')
    if context['case']=='runtime_failure':
        if host['runtime']['kind'] not in ('pipeline','pipeline-fixture'):
            return dict(supported=False,reason='worker runtime failure needs trusted same-daemon retained handles')
    targets=[]
    if context['case']=='runtime_failure' and host['runtime']['kind']=='pipeline-fixture':
        faults=host['runtime'].get('fault')
        if type(faults) is not list or not 1<=len(faults)<=3:raise ValueError('invalid preinstalled fault targets')
        for fault in faults:
            if fault.get('run_id')!=context['run_id']:continue
            if (set(fault)!={'run_id','server_id','gpu_id','after_running_seconds','role'} or
                    type(fault['gpu_id']) is not int or fault['role']!='gpu' or
                    type(fault['after_running_seconds']) not in (int,float) or
                    not 0<=fault['after_running_seconds']<3600 or
                    Decimal(str(fault['after_running_seconds']))>=Decimal(str(context['observation']['duration_seconds']))):
                raise ValueError('invalid preinstalled GPU fault declaration')
            selected=[i for i in installed if i['server_id']==fault['server_id'] and i['gpu_id']==fault['gpu_id']]
            if len(selected)!=1:raise ValueError('preinstalled fault binding is absent or ambiguous')
            binding={k:selected[0][k] for k in ('run_id','bundle_id','server_id','gpu_id','observation_sha256')}
            targets.append(dict(binding=binding,role='gpu',signal='SIGKILL',
                after_running_seconds=fault['after_running_seconds'],deployment_sha256=config['host_deployment_sha256']))
    negative=None
    if context['case']=='prepare_rollback':
        target=context['prepare_failure']
        healthy=[i['bundle_path'] for i in installed if
                 (i['server_id'],i['gpu_id'])!=(target['server_id'],target['gpu_id'])]
        for instance in installed:
            if (instance['server_id'],instance['gpu_id'])==(target['server_id'],target['gpu_id']):
                negative=negative_fixture(instance,context,healthy)
            else:
                _manifest(Path(instance['bundle_path']))
                if sha(Path(instance['bundle_path'])/'MANIFEST.sha256')!=context['bundle_manifest_sha256']:
                    raise ValueError('healthy bundle differs from frozen baseline')
    reply=dict(supported=True,reason=None,coverage=[],fault_targets=targets,receiver_instances=[
        [i['server_id'],i['gpu_id']] for i in installed])
    if negative is not None:reply['negative_fixture']=negative
    return reply


class SenderSession:
    def __init__(self,config,context,supervisor):
        self.config=config;self.context=context;self.supervisor=supervisor
        self.jobs=check_sender(config,context)
        self.reports=[];self.started=False

    def arm(self):
        if self.started:return
        self.started=True  # Uncertain launch never permits a second attempt.
        for index,item in enumerate(self.jobs):
            job=item['job']
            owner={k:job[k] for k in ('run_id','bundle_id','server_id','gpu_id','observation_sha256')}
            owner['generation']=hashlib.sha256(_encode(dict(context=self.context,job=job))).hexdigest()
            report=self.supervisor.launch(owner,'sender-'+str(index),item['argv'],
                str(Path(job['config_path']).parent),{})
            self.reports.append(report)

    def collect(self):
        rows=[];artifacts=[]
        for index,prior in enumerate(self.reports):
            report=self.supervisor.wait(prior['identity'],0)
            self.reports[index]=report
            stats={}
            if report['state']=='EXITED' and report['returncode']==0:
                try:stats=read(report['logs']['stdout'])
                except (ValueError,OSError):stats=dict(stats_error='missing or invalid sender JSON')
            for path in report['logs'].values():
                if Path(path).exists():artifacts.append(dict(path=path,sha256=sha(path)))
            job=self.jobs[index]['job']
            rows.append(dict(server_id=job['server_id'],gpu_id=job['gpu_id'],
                station_ids=self.jobs[index]['stations'],state=report['state'],returncode=report['returncode'],
                identity=report['identity'],stats=stats,config_sha256=job['config_sha256'],
                expected_packets=self.jobs[index]['config']['time']['group_count']*len(self.jobs[index]['stations']),
                expected_payload_bytes=(self.jobs[index]['config']['packet']['nsamp_per_packet']*
                    self.jobs[index]['config']['packet']['nchan']*self.jobs[index]['config']['packet']['npol']*2+32)*
                    self.jobs[index]['config']['time']['group_count']*len(self.jobs[index]['stations']),
                groups=self.jobs[index]['config']['time']['group_count'],schema_version=self.jobs[index]['config']['schema_version']))
        return dict(senders=rows,artifacts=artifacts)

    def cleanup(self):
        for index,report in enumerate(self.reports):
            report=self.supervisor.wait(report['identity'],0)
            if report['state']=='RUNNING':
                self.supervisor.signal(report['identity'],signal.SIGTERM)
                report=self.supervisor.wait(report['identity'],0.2)
            if report['state']=='RUNNING':
                self.supervisor.signal(report['identity'],signal.SIGKILL)
                report=self.supervisor.wait(report['identity'],1)
            self.reports[index]=report
        gone=(len(self.reports)==len(self.jobs) and all(r['state'] in ('EXITED','NOT_STARTED')
               and (type(r['returncode']) is int or r['state']=='NOT_STARTED') for r in self.reports))
        # Exact SHA-bound leaf binary plus retained child reaps supplies descendant proof.
        return dict(sender_processes_gone=gone,processes_gone=gone,descendants_gone=gone,
                    resources_gone=True,host_supervisors_gone=True)


def sender_root(config,context):
    key=hashlib.sha256(_encode(dict(run_id=context['run_id'],identity=context['identity']))).hexdigest()
    return str(Path(config['root'])/key)


def sender_daemon(config,context,token):
    root=PrivateRoot(sender_root(config,context));supervisor=None;session=None
    try:
        with root.lock('daemon.lock',blocking=False):
            launch=root.read('launch.json')
            if launch!=dict(config=config,context=context,token=token):raise ValueError('launch changed')
            supervisor=ProcessSupervisor(str(Path(sender_root(config,context))/'processes'))
            session=SenderSession(config,context,supervisor)
            daemon_identity=_read_process(os.getpid())
            cleanup=None
            failure=None
            try:
                session.arm()
                deadline=time.monotonic()+config['lifetime_seconds']
                while True:
                    observed=session.collect()
                    failed=[s for s in observed['senders'] if s['state'] not in ('RUNNING','EXITED') or
                            (s['state']=='EXITED' and s['returncode']!=0)]
                    if failed:failure=failed[0]
                    root.write('status.json',dict(phase='RUNNING',daemon_identity=daemon_identity,
                                                 failure=failure,**observed))
                    if (root.read('stop.json') is not None or time.monotonic()>=deadline or
                            failure is not None or all(s['state']!='RUNNING' for s in observed['senders'])):break
                    time.sleep(0.02)
            finally:
                cleanup=session.cleanup()
                root.write('status.json',dict(phase='EXITED',daemon_identity=daemon_identity,
                                             failure=failure,**session.collect(),cleanup=cleanup))
                supervisor.close()
    finally:
        root.close()


def sender_exchange(config,context,operation,deployment_path):
    # check is read-only and does not create a directory, process or launch intent.
    if operation=='check':return check(config,context)
    if operation=='inject':
        # GPU injection is already armed by the receiver's private deployment.
        # Sender participation is read-only; client targets/PIDs never act here.
        check_sender(config,context)
        return dict(applied=True,disposition='NO_SENDER_EFFECT')
    path=sender_root(config,context)
    if operation!='arm' and not Path(path).exists():
        return (dict(sender_processes_gone=True,processes_gone=True,descendants_gone=True,
                     resources_gone=True,host_supervisors_gone=True,artifacts=[])
                if operation=='cleanup' else dict(senders=[],artifacts=[]))
    global_root=PrivateRoot(config['root']);root=PrivateRoot(path)
    try:
        if operation=='arm':
            jobs=check_sender(config,context)
            start=datetime.datetime.strptime(context['observation']['utc_start'],'%Y-%m-%d-%H:%M:%S').replace(tzinfo=datetime.timezone.utc)
            remaining=(start-datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            if remaining<=0 or remaining+float(context['observation']['duration_seconds'])+1>=config['lifetime_seconds']:
                raise ValueError('sender finite deadline does not cover immutable future schedule')
            with global_root.lock('launch.lock'):
                # One host-level namespace protects sender port reuse across runs.
                for name in global_root.names():
                    if len(name)==64 and name!=Path(path).name:
                        other=PrivateRoot(str(Path(config['root'])/name))
                        try:
                            prior=other.read('status.json')
                            if prior is None or prior['phase']!='EXITED':raise ValueError('another sender run unresolved')
                        finally:other.close()
                prior=root.read('launch.json')
                if prior is None:
                    token=uuid.uuid4().hex
                    root.write('launch.json',dict(config=config,context=context,token=token))
                    root.write('context.json',context)
                    fd=root._open('daemon.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND)
                    try:
                        subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),
                            '--deployment',deployment_path,'--daemon','--context',str(Path(path)/'context.json'),
                            '--launch-token',token],stdin=subprocess.DEVNULL,stdout=fd,stderr=fd,
                            env={},cwd=config['root'],start_new_session=True,close_fds=True,shell=False)
                    finally:os.close(fd)
                elif prior['config']!=config or prior['context']!=context:raise ValueError('sender launch content conflict')
            # Intent is not acknowledgement: wait for daemon-owned observations.
        elif operation=='cleanup':
            root.write('stop.json',dict(run_id=context['run_id']))
        elif operation not in ('collect','health','retire'):raise ValueError('unsupported sender operation')
        deadline=time.monotonic()+min(60,config['lifetime_seconds']+2)
        while True:
            observed=root.read('status.json')
            if observed and (operation not in ('cleanup','retire') or observed['phase']=='EXITED'):break
            if time.monotonic()>=deadline:raise TimeoutError('sender daemon outcome unresolved')
            time.sleep(0.02)
        if operation=='arm':return dict(applied=True)
        if operation in ('cleanup','retire'):
            # daemon may still be writing its final marker; lock release proves return.
            while True:
                try:
                    with root.lock('daemon.lock',blocking=False):pass
                    break
                except BlockingIOError:
                    if time.monotonic()>=deadline:raise TimeoutError('sender daemon retirement unresolved')
                    time.sleep(0.01)
            return dict(observed['cleanup'],artifacts=observed['artifacts'])
        return dict(senders=observed['senders'],artifacts=observed['artifacts'],failure=observed.get('failure'))
    finally:
        root.close();global_root.close()


def read_fault_record(owner,snapshot,claim,processes,path):
    """Read the fixed proof; never signal/adopt a PID from this evidence."""
    if not path.exists():raise ValueError('fixed GPU fault.json is missing')
    proof=read(path)
    gpu=[p for p in processes if p.get('role')=='gpu']
    if len(gpu)!=1:raise ValueError('fixed GPU process ledger is missing or ambiguous')
    process=gpu[0];fingerprint=process.get('process',{})
    fields={'pid','start_ticks','boot_id','uid','pgid','sid'}
    if (set(fingerprint)!=fields or
            any(type(fingerprint.get(k)) is not int or fingerprint[k]<0 for k in fields-{'boot_id'}) or
            any(fingerprint.get(k)==0 for k in ('pid','pgid','sid')) or
            type(fingerprint.get('boot_id')) is not str or not fingerprint['boot_id'] or
            bytes(_encode(process.get('owner')))!=bytes(_encode(owner))):
        raise ValueError('fixed GPU process identity is incomplete or foreign')
    expected=dict(owner=owner,role='gpu',signal='SIGKILL',identity=dict(fingerprint,owner=owner,role='gpu'))
    if bytes(_encode(proof))!=bytes(_encode(expected)):
        raise ValueError('fault.json does not match retained GPU identity')
    if (snapshot['state']!='FAILED' or process['state']!='EXITED' or
            type(process['returncode']) is not int or process['returncode']!=-signal.SIGKILL):
        raise ValueError('fixed GPU lacks authoritative SIGKILL failure evidence')
    cleanup=claim.get('cleanup_evidence',{})
    if (claim.get('released') is not True or bytes(_encode(claim.get('owner')))!=bytes(_encode(owner)) or
            bytes(_encode(cleanup.get('owner')))!=bytes(_encode(owner)) or
            bytes(_encode(cleanup.get('resources')))!=bytes(_encode(claim.get('resources'))) or
            any(cleanup.get(k) is not True for k in ('processes_gone','descendants_gone','resources_gone'))):
        raise ValueError('fixed GPU owner cleanup is unconfirmed')
    return dict(server_id=owner['server_id'],gpu_id=owner['gpu_id'],generation=owner['generation'],
                proof=proof,artifact=dict(path=str(path),sha256=sha(path)))


def receiver_collect(config,context,cleanup=False):
    host=load_deployment(config['host_deployment_path'])
    negative=[];targets=[];fault_records=[]
    if context['case'] in ('prepare_rollback','runtime_failure'):
        checked=check(config,context)
        if checked.get('negative_fixture'):negative.append(checked['negative_fixture'])
        targets=checked.get('fault_targets',[])
    if not Path(host['root']).is_dir():raise ValueError('receiver root absent; no audit creation')
    root=PrivateRoot(host['root']);connection=None
    instances=[];launches=[];artifacts=[];clean=True;audit=[]
    try:
        selected=[i for i in host['instances'] if i['run_id']==context['run_id']]
        journal_path=Path(host['root'])/'journal.sqlite'
        if journal_path.exists():
            connection=sqlite3.connect(journal_path.as_uri()+'?mode=ro',uri=True)
            connection.execute('BEGIN')
        objects=LinuxPlatform._objects() if cleanup or context['case']=='prepare_rollback' else {}
        for installed in selected:
            snapshot=root.read('instance-'+instance_key(installed))
            if snapshot is None:continue
            owner=snapshot['owner']
            if any(owner[k]!=installed[k] for k in ('run_id','bundle_id','server_id','gpu_id','observation_sha256')):
                raise ValueError('foreign receiver owner')
            if connection is None:raise ValueError('owner without journal')
            row=connection.execute('SELECT document FROM owners WHERE identity=?',(journal_encode(owner),)).fetchone()
            if row is None:raise ValueError('owner claim absent')
            record=_decode(row[0].encode('utf-8'))
            clean=clean and record['released'] and snapshot['cleanup']=='CONFIRMED'
            processes=[]
            process_root=Path(host['root'])/'processes'
            for path in process_root.glob('*/launch.json'):
                process=read(path)
                if process['owner']==owner:
                    processes.append(process)
                    if process['state'] not in ('EXITED','NOT_STARTED') or (process['state']=='EXITED' and type(process['returncode']) is not int):
                        clean=False
            audit.append(dict(snapshot=snapshot,claim=record,processes=processes))
            actions=list(record['actions'].values())
            launches.append(dict(server_id=owner['server_id'],gpu_id=owner['gpu_id'],generation=owner['generation'],
                prepare_count=sum(a['command']['operation']=='prepare' for a in actions),
                start_count=sum(a['command']['operation']=='start' for a in actions)))
            directory=Path(record['resources']['run_dir'])
            if context['case']=='runtime_failure':
                launches[-1].update(claim=record,processes=processes)
                key=(owner['server_id'],owner['gpu_id'])
                expected=[t for t in targets if (t['binding']['server_id'],t['binding']['gpu_id'])==key]
                fault=directory/'fault.json'
                if not cleanup:
                    if len(expected)>1:raise ValueError('ambiguous preinstalled GPU target')
                    if expected:
                        fault_records.append(read_fault_record(owner,snapshot,record,processes,fault))
                    elif fault.exists():raise ValueError('fixed fault record exists on an unconfigured owner')
            if context['case']=='prepare_rollback':
                tokens=[]
                for role in ('raw','compute','output'):
                    path=directory/('ring-'+role+'.objects')
                    if path.exists():tokens.append(dict(role=role,token=read(path)))
                launches[-1].update(claim=record,processes=processes,ring_tokens=tokens,
                    ipc_absent=all((kind,key) not in objects for key in record['resources']['ipc_keys']
                                   for kind in ('shm','sem')))
            for name in ('owner.json','receiver.result','unpack.result','gpu.result','fault.json',
                         'ring-raw.objects','ring-compute.objects','ring-output.objects'):
                path=directory/name
                if path.exists():artifacts.append(dict(path=str(path),sha256=sha(path)))
            if snapshot['state']=='COMPLETED':
                receiver,unpack,gpu=(read(directory/(r+'.result')) for r in ('receiver','unpack','gpu'))
                report=read(directory/'compiled/validation_report.json')
                for role,value in [('receiver',receiver),('unpack',unpack),('gpu',gpu)]:
                    process=next(p for p in processes if p['role']==role)
                    if value['pid']!=process['process']['pid'] or any(value[k]!=report[k] for k in ('config_id','geometry_id')):
                        raise ValueError('product result identity mismatch')
                raw_bytes=report['derived']['raw_record_bytes']
                if receiver['committed_bytes']%raw_bytes:raise ValueError('partial receiver record')
                rings=read(directory/'compiled/ring_plan.json')['rings']
                samples=report['derived']['expected_groups']*context['samples_per_packet']
                # The bundle time-grid is authoritative and immutable in the context.
                block_samples=context['samples_per_block']
                expected_input=rings['compute']['block_bytes']*samples//block_samples
                expected_output=rings['output']['block_bytes']*samples//block_samples
                instances.append(dict(server_id=owner['server_id'],gpu_id=owner['gpu_id'],
                    unpack_output_bytes=unpack['emitted_bytes'],expected_input_bytes=expected_input,
                    expected_output_bytes=expected_output,expected_packets=report['derived']['expected_groups']*len(context['observation']['station_ids']),
                    published_blocks=gpu['published_blocks'],expected_blocks=(samples+block_samples-1)//block_samples,
                    receiver_packets=receiver['committed_bytes']//raw_bytes,
                    unpack_packets=sum(unpack['station_accepted_packets']),input_bytes=gpu['input_bytes'],
                    output_bytes=gpu['output_bytes'],eod=receiver['eod'] and unpack['eod'] and gpu['output_eod']))
            if cleanup and record['released']:
                evidence=record['cleanup_evidence']
                clean=clean and all(evidence[k] is True for k in ('processes_gone','descendants_gone','resources_gone'))
                clean=clean and all((kind,key) not in objects for key in record['resources']['ipc_keys'] for kind in ('shm','sem'))
        if context['case']=='runtime_failure' and not cleanup and len(fault_records)!=len(targets):
            raise ValueError('preinstalled GPU fault proof is missing')
        # Snapshot the read transaction, never hash a live shared SQLite database.
        audit_root=PrivateRoot(config['root'])
        try:
            name=('cleanup-' if cleanup else 'collect-')+hashlib.sha256(_encode(context)).hexdigest()+'-'+uuid.uuid4().hex+'.json'
            value=dict(context=context,owners=audit,fault_targets=targets,fault_records=fault_records,
                ipc_objects=[dict(kind=kind,key=key,metadata=row)
                for (kind,key),row in sorted(objects.items()) if any(key in i['resources']['ipc_keys'] for i in selected)])
            old=audit_root.read(name)
            if old is None:audit_root.write(name,value)
            elif old!=value:raise ValueError('receiver audit changed; preserve original')
            audit_path=str(Path(config['root'])/name)
            artifacts.append(dict(path=audit_path,sha256=sha(audit_path)))
        finally:audit_root.close()
        if cleanup:return dict(processes_gone=bool(clean),descendants_gone=bool(clean),resources_gone=bool(clean),
                               sender_processes_gone=True,host_supervisors_gone=False,artifacts=artifacts)
        artifacts.extend(f['artifact'] for f in fault_records if f['artifact'] not in artifacts)
        return dict(instances=instances,launches=launches,artifacts=artifacts,negative_fixtures=negative,
                    fault_targets=targets,fault_records=fault_records)
    finally:
        if connection:connection.close()
        root.close()


def receiver_retire(config):
    # Fixed operator control; never signals a PID or adds an observation operation.
    host=load_deployment(config['host_deployment_path'])
    argv=[sys.executable,'-B',str(ROOT/'scripts/multiserver_host.py'),
          '--deployment',config['host_deployment_path'],'--retire']
    if host['runtime']['kind']=='pipeline-fixture':argv.append('--development-fixture')
    result=subprocess.run(argv,
                          stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                          timeout=65,env={},check=False)
    if len(result.stdout)>_LIMIT:raise ValueError('retire response too large')
    reply=_decode(result.stdout)
    confirmed=result.returncode==0 and reply.get('state')=='RETIRED' and reply.get('cleanup')=='CONFIRMED'
    return dict(host_supervisors_gone=confirmed)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment',required=True)
    parser.add_argument('--daemon',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--context',help=argparse.SUPPRESS)
    parser.add_argument('--launch-token',help=argparse.SUPPRESS)
    args=parser.parse_args()
    try:
        fd=os.open(args.deployment,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:_private(fd)
        finally:os.close(fd)
        config=read(args.deployment)
        host_fields={'root','stop_timeout_seconds','request_timeout_seconds','runtime','instances'}
        if set(config)==host_fields:
            # Role/flag is selected solely by a fully validated private operator file.
            host=load_deployment(args.deployment)
            if host['runtime'].get('kind')!='pipeline-fixture' or args.daemon or args.context or args.launch_token:
                raise ValueError('fixed host fixture deployment required')
            import multiserver_host
            sys.argv=[str(ROOT/'scripts/multiserver_host.py'),'--deployment',args.deployment,'--development-fixture']
            return multiserver_host.main()
        config=validate_config(config)
        if args.daemon:
            sender_daemon(config,read(args.context),args.launch_token);return 0
        command=read_stdin()
        if (set(command)!={'schema_version','operation','server_id','gpu_id','context'} or
                type(command['schema_version']) is not int or command['schema_version']!=1 or
                command['server_id']!=config['server_id'] or type(command['gpu_id']) is not int or command['gpu_id']!=0):
            raise ValueError('invalid peer command')
        context=command['context'];operation=command['operation']
        if context['identity']!=config['identity']:raise ValueError('peer identity mismatch')
        if operation not in ('check','arm','inject','health','collect','cleanup','retire'):raise ValueError('unknown peer operation')
        if operation=='check':reply=check(config,context)
        elif config['role']=='sender':reply=sender_exchange(config,context,operation,args.deployment)
        elif operation=='inject':
            checked=check(config,context)
            if not checked['supported']:raise ValueError('trusted same-daemon fault not armed')
            reply=dict(applied=True)
        elif operation=='arm':reply=dict(applied=True)
        elif operation=='health':reply=dict(senders=[])
        elif operation=='retire':reply=receiver_retire(config)
        else:reply=receiver_collect(config,context,cleanup=operation=='cleanup')
        reply.update(identity=context['identity'],run_id=context['run_id'],role=config['role'])
        sys.stdout.buffer.write(_encode(reply));sys.stdout.buffer.flush();return 0
    except Exception as error:
        print('acceptance peer failed: '+type(error).__name__,file=sys.stderr);return 2


if __name__=='__main__':sys.exit(main())
