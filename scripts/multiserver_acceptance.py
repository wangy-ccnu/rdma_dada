#!/usr/bin/env python3
"""Bounded MS-07 functional acceptance on HF (Python 3.8, standard library).

Usage: python3 -B scripts/multiserver_acceptance.py --suite /absolute/suite.json
       --output /feature/results/campaign [--resume]

Only the production ObservationController dispatches pipeline lifecycle commands.
The fixed tests/fixtures/multiserver_acceptance_peer.py owns finite senders and
reads receiver evidence. Endpoints/deployments are trusted operator inputs; no
client argv, shell, PID kill, paper runner or dynamically imported plugin exists.

Suite v1: suite_id, identity {source_revision,source_manifest_sha256,build_id},
endpoints (SSHHostAdapter map), ssh_path, transport_timeout_seconds,
poll_interval_seconds, case_timeout_seconds, cleanup_timeout_seconds, cases.
Each case has name and exactly three repetitions, each {run_id,bundle_path,
global_path}. Paths refer to preinstalled immutable bundles on HF. All run IDs
must also be preinstalled on the receiver's SINGLE shared host journal/deployment.
Optional fixture_endpoints maps trusted SSH peer IDs to endpoint maps; peer config
is selected only by endpoint deployment_path. Every peer echoes identity/run/case.
No fixture means UNSUPPORTED, never dry-run READY or PASS. runtime_failure requires the trusted fixed FaultPipelineRuntime in the host daemon;
sender failure is never treated as that case.
Its expected target comes from the pinned host deployment, never participant
order. Success requires that exact failed owner, a matching fixed fault.json and
authoritative GPU SIGKILL ledger exit, plus completion of every healthy peer.

All schedules are fixed in the bundles and sender configs, with sufficient future
UTC reservation for sequential cases. No rescheduling changes immutable inputs.
Only formal observation-window packets are sent; pre-start readiness does not
include preparation traffic. prepare_rollback repetitions additionally declare
prepare_failure={server_id,gpu_id,expected_error:'BUNDLE_MANIFEST'} and require a
private, preinstalled independent negative-copy identity sidecar on the host.
An exclusive campaign lock serializes callers. append-only events persist intent
BEFORE effects. --resume reuses final evidence, reconciles and stops unfinished
runs, and NEVER replays prepare/start/sender arm/injection. It marks interrupted
verification INCOMPLETE; a new campaign is required to measure that case again.
Cleanup facts cannot overwrite TEST_RESULT or first_failure. Failed cleanup stops
new cases. Evidence and controller DBs are retained, never deleted by the runner.
Failure evidence separates PRODUCT_FAIL, INPUT_FAIL, HARNESS_FAIL and ENV_BLOCKED;
the summary preserves the first failure and counts every classification. Sender
health is queried between bounded controller requests; any authoritative sender
failure stops the whole observation, unlike an isolated GPU runtime failure.
"""
import argparse
import copy
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
from decimal import Decimal
import re
import sys
import time
import tempfile

try:
    from .multiserver.controller import ObservationController, ControllerError
    from .multiserver.transport import SSHHostAdapter, _decode
except ImportError:
    from multiserver.controller import ObservationController, ControllerError
    from multiserver.transport import SSHHostAdapter, _decode

CASES = ('single', 'samehost_equal', 'samehost_extra', 'all_ready', 'prepare_rollback',
         'finite_eod', 'manual_graceful', 'manual_force', 'runtime_failure', 'disconnect_reconnect')
LIMIT = 1024 * 1024


FAILURE_CLASSES=('PRODUCT_FAIL','INPUT_FAIL','HARNESS_FAIL','ENV_BLOCKED')


class AcceptanceFailure(ValueError):
    def __init__(self,classification,detail):
        if classification not in FAILURE_CLASSES:
            raise ValueError('invalid acceptance failure classification')
        self.classification=classification
        super().__init__(detail)


class SenderFailure(AcceptanceFailure):
    """A configured input sender failed while the transfer was active."""
    def __init__(self,detail):
        super().__init__('INPUT_FAIL',detail)


def failure_result(classification):
    return 'FAIL' if classification=='PRODUCT_FAIL' else classification


class RecordingAdapter:
    """Observe the controller's dispatches without changing order or commands."""
    def __init__(self,runner):
        self.runner=runner

    def send(self,key,command):
        report=self.runner.adapter.send(key,command)
        entry=dict(instance=list(key),command=copy.deepcopy(command),report=copy.deepcopy(report))
        self.runner._host_trace.append(entry)
        self.runner._event('host_report',**entry)
        return report


def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,
                       allow_nan=False)+'\n').encode('utf-8')


def read_json(path):
    with Path(path).open('rb') as stream:
        raw=stream.read(LIMIT+1)
    if len(raw)>LIMIT:
        raise ValueError('JSON exceeds acceptance limit')
    return _decode(raw)


def _absolute(value):
    if type(value) is not str or not os.path.isabs(value) or '\x00' in value:
        raise ValueError('absolute installed path required')
    return value


def load_suite(value):
    suite=read_json(value) if not isinstance(value,dict) else copy.deepcopy(value)
    canonical(suite)
    fields={'schema_version','suite_id','identity','endpoints','ssh_path','transport_timeout_seconds',
            'poll_interval_seconds','case_timeout_seconds','cleanup_timeout_seconds','cases'}
    if set(suite)-{'fixture_endpoints'}!=fields or type(suite['schema_version']) is not int or suite['schema_version']!=1:
        raise ValueError('invalid suite version/fields')
    if type(suite['suite_id']) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,80}',suite['suite_id']):
        raise ValueError('invalid suite ID')
    identity=suite['identity']
    if (type(identity) is not dict or set(identity)!={'source_revision','source_manifest_sha256','build_id'} or
            any(type(v) is not str or not v for v in identity.values()) or
            not re.fullmatch('[0-9a-f]{64}',identity['source_manifest_sha256'])):
        raise ValueError('explicit source/build identity required')
    _absolute(suite['ssh_path'])
    for field in ('transport_timeout_seconds','poll_interval_seconds','case_timeout_seconds','cleanup_timeout_seconds'):
        n=suite[field]
        if type(n) not in (int,float) or not math.isfinite(n) or not 0<n<=3600:
            raise ValueError('invalid bounded '+field)
    if type(suite['endpoints']) is not dict or not suite['endpoints']:
        raise ValueError('trusted host endpoints required')
    if type(suite['cases']) is not list or not suite['cases']:
        raise ValueError('nonempty cases required')
    names=set();runs=set()
    for case in suite['cases']:
        if set(case)!={'name','repetitions'} or case['name'] not in CASES or case['name'] in names:
            raise ValueError('unknown or duplicate case')
        names.add(case['name'])
        repetitions=case['repetitions']
        if type(repetitions) is not list or len(repetitions)!=3:
            raise ValueError('exactly three independent repetitions required')
        for item in repetitions:
            required={'run_id','bundle_path','global_path'}
            if case['name']=='prepare_rollback':required.add('prepare_failure')
            if set(item)!=required:
                raise ValueError('invalid installed repetition')
            if case['name']=='prepare_rollback':
                failure=item['prepare_failure']
                if (type(failure) is not dict or set(failure)!={'server_id','gpu_id','expected_error'} or
                        type(failure['server_id']) is not str or not failure['server_id'] or
                        type(failure['gpu_id']) is not int or failure['gpu_id']<0 or
                        failure['expected_error']!='BUNDLE_MANIFEST'):
                    raise ValueError('invalid fixed prepare failure expectation')
            if type(item['run_id']) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,100}',item['run_id']) or item['run_id'] in runs:
                raise ValueError('unique safe run IDs required')
            runs.add(item['run_id'])
            for field in ('bundle_path','global_path'):
                _absolute(item[field])
    return suite


def _write_new(path,value):
    with Path(path).open('xb') as stream:
        os.chmod(str(path),0o600)
        stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())


class PeerFixture:
    """Only the fixed, versioned sender/readonly receiver peer protocol."""
    def __init__(self,suite):
        self.adapter=SSHHostAdapter(suite['fixture_endpoints'],ssh_path=suite['ssh_path'],
                                    timeout_seconds=suite['transport_timeout_seconds'])
        self.peers=sorted(suite['fixture_endpoints'])

    def perform(self,operation,context):
        reports=[];failures=[]
        for peer in self.peers:
            command=dict(schema_version=1,operation=operation,server_id=peer,gpu_id=0,
                         context=context)
            try:
                response=self.adapter.send((peer,0),command)
                if response.get('identity')!=context['identity'] or response.get('run_id')!=context['run_id']:
                    raise ValueError('fixture response binding mismatch')
                reports.append(response)
            except Exception as error:
                if operation not in ('cleanup','retire'):raise
                failures.append(dict(peer=peer,error=str(error)))
        if operation=='retire':
            return dict(identity=context['identity'],host_supervisors_gone=not failures and all(
                r.get('host_supervisors_gone') is True for r in reports),failures=failures)
        if operation=='check':
            if any(r.get('supported') is not True for r in reports):
                return dict(identity=context['identity'],supported=False,
                            reason='; '.join(r['reason'] for r in reports if r.get('reason')))
            coverage=[(j['server_id'],j['gpu_id'],station) for r in reports for j in r.get('coverage',[])
                      for station in j['station_ids']]
            expected={(j['server_id'],j['gpu_id'],station) for j in context['assignments']
                      for station in context['observation']['station_ids']}
            receivers=[tuple(j) for r in reports for j in r.get('receiver_instances',[])]
            if len(set(coverage))!=len(coverage) or set(coverage)!=expected or len(set(receivers))!=len(receivers) or set(receivers)!={k[:2] for k in expected}:
                raise ValueError('sender Station/receiver coverage mismatch')
            return dict(identity=context['identity'],supported=all(r.get('supported') is True for r in reports),
                        reason='; '.join(r['reason'] for r in reports if r.get('reason')) or None,
                        fault_targets=[f for r in reports for f in r.get('fault_targets',[])],
                        negative_fixtures=[r['negative_fixture'] for r in reports if r.get('negative_fixture')])
        if operation=='collect':
            receivers=[r for r in reports if r['role']=='receiver']
            senders=[row for r in reports if r['role']=='sender' for row in r.get('senders',[])]
            instances=[row for r in receivers for row in r.get('instances',[])]
            for row in instances:
                selected=[s for s in senders if s['server_id']==row['server_id'] and s['gpu_id']==row['gpu_id']]
                row['sender_packets']=sum(s['stats'].get('sent_packets',0) for s in selected)
                row['sender_stats']=selected
                if context['case'] not in ('manual_graceful','manual_force','prepare_rollback'):
                    for sender in selected:
                        stats=sender['stats']
                        if stats.get('sent_packets')!=sender['expected_packets'] or stats.get('scheduled_packets')!=sender['expected_packets'] or stats.get('payload_bytes')!=sender['expected_payload_bytes'] or stats.get('failed_packets')!=0:
                            raise SenderFailure('sender count/byte closure failed')
                        if sender['schema_version']==3:
                            if stats.get('station_ids')!=sender['station_ids']:raise SenderFailure('sender Station identity mismatch')
                            station_counts=stats.get('station_counts',[])
                            if len(station_counts)!=len(sender['station_ids']) or [r['station_id'] for r in station_counts]!=sender['station_ids'] or any(r['sent_packets']!=sender['groups'] or r['scheduled_packets']!=sender['groups'] for r in station_counts):
                                raise SenderFailure('sender Station count mismatch')
                if context['case'] not in ('manual_graceful','manual_force','prepare_rollback') and (not selected or any(s['state']!='EXITED' or s['returncode']!=0 for s in selected)):
                    raise SenderFailure('sender did not finish authoritatively')
            return dict(identity=context['identity'],instances=instances,
                        launches=[row for r in receivers for row in r.get('launches',[])],
                        negative_fixtures=[row for r in receivers for row in r.get('negative_fixtures',[])],
                        fault_targets=[row for r in receivers for row in r.get('fault_targets',[])],
                        fault_records=[row for r in receivers for row in r.get('fault_records',[])],
                        artifacts=[row for r in reports for row in r.get('artifacts',[])])
        if operation=='health':
            senders=[row for report in reports if report['role']=='sender'
                     for row in report.get('senders',[])]
            failures=[report['failure'] for report in reports if report.get('failure')]
            bad=[row for row in senders if row['state'] not in ('RUNNING','EXITED') or
                 (row['state']=='EXITED' and row['returncode']!=0)]
            classification=('INPUT_FAIL' if any(row['state']=='EXITED' and
                type(row['returncode']) is int and row['returncode']!=0 for row in senders)
                else 'HARNESS_FAIL')
            return dict(identity=context['identity'],healthy=bool(senders) and not failures and not bad,
                        senders=senders,failures=failures or bad,failure_classification=classification)
        if operation=='cleanup':
            flags=('processes_gone','resources_gone','descendants_gone','host_supervisors_gone','sender_processes_gone')
            return dict(identity=context['identity'],**{k:not failures and all(r.get(k) is True for r in reports) for k in flags},
                        failures=failures,peers=reports,artifacts=[row for r in reports for row in r.get('artifacts',[])])
        if any(r.get('applied') is not True for r in reports):
            raise ValueError('fixture effect outcome uncertain')
        return dict(identity=context['identity'],applied=True)


class AcceptanceRunner:
    def __init__(self,suite,output,*,host_adapter=None,fixture=None,resume=False,
                 utc_now=None,monotonic_ns=None,sleep=time.sleep):
        self.suite=load_suite(suite)
        self.output=Path(output).absolute()
        self.adapter=host_adapter
        self.fixture=fixture
        self.resume=resume
        self.utc_now=utc_now or (lambda:datetime.datetime.now(datetime.timezone.utc))
        self.monotonic_ns=monotonic_ns or time.monotonic_ns
        self.sleep=sleep
        self.controller=None
        self.evidence=None
        self.event_path=None
        self.inputs={}
        self._senders_armed=False
        self._active_context=None
        self._host_trace=[]

    def _event(self,kind,**data):
        value=dict(event=kind,monotonic_ns=self.monotonic_ns(),**data)
        with self.event_path.open('ab') as stream:
            stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())
        return value

    def _open(self,path):
        return ObservationController(path,RecordingAdapter(self),utc_now=self.utc_now,
            monotonic_ns=self.monotonic_ns,heartbeat_timeout_ns=60*10**9,
            drain_timeout_ns=int(self.suite['cleanup_timeout_seconds']*10**9))

    def _request(self,operation,mode=None):
        request=dict(schema_version=1,run_id=self.evidence['run_id'],operation=operation,
                     request_id='acceptance-'+operation+('-'+mode if mode else ''))
        if mode:request['mode']=mode
        self._event('controller_intent',request=request)
        try:
            result=self.controller.execute(request)
        except ControllerError as error:
            if error.code=='MISSED_START':
                raise AcceptanceFailure('ENV_BLOCKED',str(error)) from error
            raise
        self._event('controller_reply',result=result)
        return result['status']

    def _fixture(self,operation,context):
        self._event('fixture_intent',operation=operation)
        try:
            value=self.fixture.perform(operation,context)
        except AcceptanceFailure:
            raise
        except (FileNotFoundError,PermissionError) as error:
            classification='ENV_BLOCKED' if operation=='check' else 'HARNESS_FAIL'
            raise AcceptanceFailure(classification,str(error)) from error
        except Exception as error:
            raise AcceptanceFailure('HARNESS_FAIL',str(error)) from error
        if value.get('identity')!=context['identity']:
            raise ValueError('fixture identity mismatch')
        self._event('fixture_reply',operation=operation,result=value)
        return value

    def _reconcile(self):
        value=self.controller.reconcile(self.evidence['run_id'])
        self._event('status',status=value)
        return value

    def _until(self,predicate,timeout):
        deadline=self.monotonic_ns()+int(timeout*1e9)
        while True:
            self._sender_health()
            status=self._reconcile()
            self._sender_health()
            if predicate(status):return status
            if status['phase']=='FINISHED':
                raise AcceptanceFailure('PRODUCT_FAIL','unexpected terminal lifecycle '+str(status['result']))
            if self.monotonic_ns()>=deadline:
                classification='HARNESS_FAIL' if status['unknown_instances'] else 'PRODUCT_FAIL'
                raise AcceptanceFailure(classification,'functional lifecycle deadline exceeded')
            self.sleep(self.suite['poll_interval_seconds'])

    def _sender_health(self):
        if self._senders_armed:
            report=self._fixture('health',self._active_context)
            if report.get('healthy') is not True:
                if report.get('failure_classification')=='INPUT_FAIL':
                    raise SenderFailure('configured Station sender failed; abort whole transfer')
                raise AcceptanceFailure('HARNESS_FAIL','sender health observation is missing or uncertain')

    @staticmethod
    def _topology(name,bundle):
        items=bundle['instances']
        if name=='single' and len(items)!=1:
            return 'single requires exactly one pipeline'
        if name in ('samehost_equal','samehost_extra'):
            if len(items)!=2 or items[0]['server_id']!=items[1]['server_id'] or items[0]['gpu_id']==items[1]['gpu_id']:
                return 'same-host case requires two distinct GPUs on one host'
            difference=abs(items[0]['assignment']['nchan']-items[1]['assignment']['nchan'])
            if difference!=(0 if name=='samehost_equal' else 1):return 'wrong channel split for topology'
        if name in ('prepare_rollback','runtime_failure','all_ready') and len(items)<2:
            return 'case requires at least two independent participants'
        return None

    def _verify_collection(self,collection,status,name):
        expected={(i['server_id'],i['gpu_id']):i for i in status['instances']}
        rows=collection['instances']
        if len({(i['server_id'],i['gpu_id']) for i in rows})!=len(rows):
            raise ValueError('duplicate count evidence')
        normal=name not in ('prepare_rollback','manual_graceful','manual_force')
        completed={k for k,i in expected.items() if i['state']=='COMPLETED'}
        if normal and {(i['server_id'],i['gpu_id']) for i in rows}!=completed:
            raise ValueError('missing completed-instance count evidence')
        for item in rows:
            if (item['server_id'],item['gpu_id']) not in expected:
                raise ValueError('foreign count evidence')
            if normal and (any(type(item.get(k)) is not int or item[k]<=0 for k in
                              ('sender_packets','receiver_packets','unpack_packets','input_bytes','output_bytes')) or
                           item['sender_packets']!=item['receiver_packets'] or
                           item['receiver_packets']!=item['unpack_packets'] or
                           item['sender_packets']!=item.get('expected_packets') or
                           item['input_bytes']!=item.get('unpack_output_bytes') or
                           item['input_bytes']!=item.get('expected_input_bytes') or
                           item['output_bytes']!=item.get('expected_output_bytes') or
                           item.get('published_blocks')!=item.get('expected_blocks') or item.get('eod') is not True):
                raise AcceptanceFailure('PRODUCT_FAIL','sender/receiver/unpack/GPU count, byte or EOD mismatch')
        launches=collection['launches']
        owned={k:i for k,i in expected.items() if i['generation'] is not None}
        if len(launches)!=len(owned):raise ValueError('missing launch/owner evidence')
        seen=set()
        for item in launches:
            key=(item['server_id'],item['gpu_id'])
            if (key not in owned or key in seen or item['generation']!=owned[key]['generation'] or
                    item['prepare_count']!=1 or (normal and item['start_count']!=1)):
                raise ValueError('duplicate launch or wrong owner generation')
            seen.add(key)
        for artifact in collection['artifacts']:
            _absolute(artifact['path'])
            if not re.fullmatch('[0-9a-f]{64}',artifact['sha256']):raise ValueError('invalid artifact SHA')
        if not collection['artifacts']:raise ValueError('raw artifact evidence missing')

    def _verify_rollback_history(self,plan):
        target=(plan['server_id'],plan['gpu_id'])
        rejected=[(index,entry) for index,entry in enumerate(self._host_trace)
                  if entry['command']['operation']=='prepare' and tuple(entry['instance'])==target]
        if len(rejected)!=1:
            raise AcceptanceFailure('PRODUCT_FAIL','designated prepare failure was not dispatched exactly once')
        index,entry=rejected[0];report=entry['report']
        if report['state']!='FAILED' or report['error']!=plan['expected_error'] or report['outcome']!='NOT_APPLIED':
            raise AcceptanceFailure('PRODUCT_FAIL','designated prepare failure reason/state mismatch')
        ready={tuple(e['instance']):e['report']['generation'] for e in self._host_trace[:index]
               if e['command']['operation']=='prepare' and e['report']['state']=='READY' and
               e['report']['outcome']=='APPLIED' and e['report']['generation'] is not None}
        if not ready:
            raise AcceptanceFailure('PRODUCT_FAIL','rollback lacked a previously READY participant')
        for key,generation in ready.items():
            stopped=any(tuple(e['instance'])==key and e['command']['operation']=='stop' and
                e['report']['generation']==generation and e['report']['state'] in ('FAILED','INTERRUPTED') and
                e['report']['cleanup']=='CONFIRMED' for e in self._host_trace[index+1:])
            if not stopped:raise AcceptanceFailure('PRODUCT_FAIL','previously READY owner lacked confirmed rollback stop')
        return ready

    def _verify_rollback_resources(self,ready,collection):
        roles={'compiler','ring-raw','ring-compute','ring-output','sink','gpu','unpack','receiver'}
        rows={(row['server_id'],row['gpu_id']):row for row in collection['launches']}
        for key,generation in ready.items():
            row=rows.get(key,{})
            claim=row.get('claim',{});owner=claim.get('owner',{})
            processes=row.get('processes',[]);rings=row.get('ring_tokens',[])
            if (owner.get('generation')!=generation or claim.get('released') is not True or
                    len(processes)!=len(roles) or {p['role'] for p in processes}!=roles or
                    any(p['owner']!=owner or p['state']!='EXITED' or type(p['returncode']) is not int
                        for p in processes) or row.get('ipc_absent') is not True):
                raise AcceptanceFailure('PRODUCT_FAIL','rollback lacked exact process/reap/IPC evidence')
            identities=[]
            for process in processes:
                identity=process.get('process',{})
                integers=('pid','start_ticks','uid','pgid','sid')
                if (any(type(identity.get(k)) is not int or identity[k]<0 for k in integers) or
                        any(identity.get(k)==0 for k in ('pid','pgid','sid')) or
                        type(identity.get('boot_id')) is not str or not identity['boot_id']):
                    raise AcceptanceFailure('PRODUCT_FAIL','rollback process identity incomplete')
                identities.append((identity['boot_id'],identity['pid'],identity['start_ticks']))
            if len(set(identities))!=len(identities) or next(p for p in processes if p['role']=='compiler')['returncode']!=0:
                raise AcceptanceFailure('PRODUCT_FAIL','rollback compiler or process identity invalid')
            cleanup=claim.get('cleanup_evidence',{})
            if cleanup.get('owner')!=owner or any(cleanup.get(k) is not True for k in
                    ('processes_gone','descendants_gone','resources_gone')):
                raise AcceptanceFailure('PRODUCT_FAIL','rollback claim release attestation incomplete')
            if len(rings)!=3 or {r['role'] for r in rings}!={'raw','compute','output'}:
                raise AcceptanceFailure('PRODUCT_FAIL','rollback lacked three created ring footprints')
            for ring in rings:
                token=ring['token'];creator=next(p for p in processes if p['role']=='ring-'+ring['role'])
                identity=token['identity']
                if (creator['returncode']!=0 or identity['owner']!=owner or not token['objects'] or
                        any(identity.get(k)!=v for k,v in creator['process'].items())):
                    raise AcceptanceFailure('PRODUCT_FAIL','rollback ring creator identity mismatch')

    def _configured_fault(self,plans,bundle,document,run_id):
        if type(plans) is not list or len(plans)!=1:
            raise AcceptanceFailure('HARNESS_FAIL','runtime failure requires exactly one preinstalled target')
        plan=plans[0]
        fields={'binding','role','signal','after_running_seconds','deployment_sha256'}
        binding=plan.get('binding',{})
        names={'run_id','bundle_id','server_id','gpu_id','observation_sha256'}
        if (set(plan)!=fields or set(binding)!=names or type(binding.get('gpu_id')) is not int or
                plan['role']!='gpu' or plan['signal']!='SIGKILL' or
                type(plan['after_running_seconds']) not in (int,float) or
                not math.isfinite(plan['after_running_seconds']) or plan['after_running_seconds']<0 or
                Decimal(str(plan['after_running_seconds']))>=Decimal(str(document['observation']['duration_seconds'])) or
                type(plan['deployment_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',plan['deployment_sha256'])):
            raise AcceptanceFailure('HARNESS_FAIL','invalid preinstalled GPU fault declaration')
        selected=[i for i in bundle['instances'] if (i['server_id'],i['gpu_id'])==
                  (binding['server_id'],binding['gpu_id'])]
        expected=(dict(run_id=run_id,bundle_id=bundle['bundle_id'],server_id=selected[0]['server_id'],
                  gpu_id=selected[0]['gpu_id'],observation_sha256=selected[0]['observation_sha256']) if len(selected)==1 else None)
        if expected is None or canonical(binding)!=canonical(expected):
            raise AcceptanceFailure('HARNESS_FAIL','fault declaration does not match installed observation binding')
        return copy.deepcopy(plan)

    def _runtime_failed_target(self,status,plan,terminal=False):
        target=(plan['binding']['server_id'],plan['binding']['gpu_id'])
        failed={(i['server_id'],i['gpu_id']) for i in status['failed_instances']}
        if failed and failed!={target}:
            raise AcceptanceFailure('PRODUCT_FAIL','runtime failure occurred on an unconfigured instance')
        peers=[i for i in status['instances'] if (i['server_id'],i['gpu_id'])!=target]
        if terminal:
            target_status=next(i for i in status['instances'] if (i['server_id'],i['gpu_id'])==target)
            if failed!={target} or target_status['state']!='FAILED' or not peers or any(i['state']!='COMPLETED' for i in peers):
                raise AcceptanceFailure('PRODUCT_FAIL','configured target failure or healthy peer completion missing')
        return failed=={target} and any(i['state']=='RUNNING' and not i['failed'] for i in peers)

    def _verify_runtime_fault(self,plan,collection,status):
        self._runtime_failed_target(status,plan,terminal=True)
        if canonical(collection.get('fault_targets',[]))!=canonical([plan]):
            raise AcceptanceFailure('HARNESS_FAIL','preinstalled fault declaration changed after preflight')
        records=collection.get('fault_records',[])
        if type(records) is not list or len(records)!=1:
            raise AcceptanceFailure('HARNESS_FAIL','fixed GPU fault proof is missing or ambiguous')
        key=(plan['binding']['server_id'],plan['binding']['gpu_id'])
        instance=next(i for i in status['instances'] if (i['server_id'],i['gpu_id'])==key)
        owner=dict(plan['binding'],generation=instance['generation'])
        record=records[0]
        if (type(record.get('gpu_id')) is not int or (record.get('server_id'),record['gpu_id'])!=key or
                record.get('generation')!=owner['generation']):
            raise AcceptanceFailure('HARNESS_FAIL','fault record target/generation mismatch')
        rows=[r for r in collection['launches'] if (r['server_id'],r['gpu_id'])==key]
        processes=rows[0].get('processes',[]) if len(rows)==1 else []
        gpu=[p for p in processes if p.get('role')=='gpu']
        if len(gpu)!=1 or canonical(gpu[0].get('owner'))!=canonical(owner):
            raise AcceptanceFailure('HARNESS_FAIL','fault GPU process ledger is missing or foreign')
        process=gpu[0];identity=process.get('process',{})
        integers=('pid','start_ticks','uid','pgid','sid')
        if (set(identity)!=set(integers)|{'boot_id'} or
                any(type(identity.get(k)) is not int or identity[k]<0 for k in integers) or
                any(identity.get(k)==0 for k in ('pid','pgid','sid')) or
                type(identity.get('boot_id')) is not str or not identity['boot_id']):
            raise AcceptanceFailure('HARNESS_FAIL','fault process fingerprint is incomplete')
        expected_identity=dict(identity,owner=owner,role='gpu')
        expected_proof=dict(owner=owner,identity=expected_identity,role='gpu',signal='SIGKILL')
        if canonical(record.get('proof'))!=canonical(expected_proof):
            raise AcceptanceFailure('HARNESS_FAIL','fixed fault proof does not match retained GPU identity')
        if process.get('state')!='EXITED' or type(process.get('returncode')) is not int or process['returncode']!=-9:
            raise AcceptanceFailure('PRODUCT_FAIL','configured GPU lacks authoritative SIGKILL exit evidence')
        claim=rows[0].get('claim',{});cleanup=claim.get('cleanup_evidence',{})
        if (canonical(claim.get('owner'))!=canonical(owner) or claim.get('released') is not True or
                canonical(cleanup.get('owner'))!=canonical(owner) or
                canonical(cleanup.get('resources'))!=canonical(claim.get('resources')) or
                any(cleanup.get(k) is not True for k in ('processes_gone','descendants_gone','resources_gone'))):
            raise AcceptanceFailure('HARNESS_FAIL','fault owner cleanup attestation missing')
        artifact=record.get('artifact',{})
        _absolute(artifact.get('path'))
        if (type(artifact.get('sha256')) is not str or not re.fullmatch('[0-9a-f]{64}',artifact['sha256']) or
                not any(canonical(a)==canonical(artifact) for a in collection['artifacts'])):
            raise AcceptanceFailure('HARNESS_FAIL','raw fixed fault artifact identity missing')

    def _failure(self,error,stage):
        classification=getattr(error,'classification','HARNESS_FAIL')
        failure=dict(stage=stage,type=type(error).__name__,detail=str(error),classification=classification)
        if self.evidence['first_failure'] is None:
            self.evidence['first_failure']=failure
            self.evidence['FAILURE_CLASSIFICATION']=classification
        self.evidence['failures'].append(failure)
        self._event('failure',failure=failure)

    def _one(self,case,item,directory,recover):
        self.evidence=dict(case=case['name'],run_id=item['run_id'],TEST_RESULT='INCOMPLETE',
                           CLEANUP_RESULT='NOT_REQUIRED',first_failure=None,failures=[],
                           FAILURE_CLASSIFICATION=None,collection=None,final_status=None)
        self.event_path=directory/'events.jsonl'
        context=dict(identity=self.suite['identity'],suite_id=self.suite['suite_id'],
                     run_id=item['run_id'],case=case['name'])
        if recover and self.event_path.exists():
            with self.event_path.open('rb') as stream:
                for line in stream:
                    event=_decode(line)
                    if event['event']=='failure':
                        self.evidence['failures'].append(event['failure'])
                        if self.evidence['first_failure'] is None:
                            self.evidence['first_failure']=event['failure']
                            classification=event['failure'].get('classification','HARNESS_FAIL')
                            self.evidence['FAILURE_CLASSIFICATION']=classification
                            self.evidence['TEST_RESULT']=failure_result(classification)
        self._event('resume' if recover else 'begin',context=context)
        inputs=self.inputs[item['run_id']]
        bundle=inputs['bundle'];document=inputs['global_document']
        self.evidence['input_identities']=inputs['input_identities']
        if not (directory/'inputs.json').exists():_write_new(directory/'inputs.json',inputs)
        context.update(bundle_id=bundle['bundle_id'],observation=document['observation'],
            bundle_manifest_sha256=inputs['input_identities']['bundle_manifest_sha256'],
            samples_per_packet=document['wire']['samples_per_packet'],samples_per_block=document['time_grid']['samples_per_block'],assignments=document['assignments'],
            destinations=[dict(server_id=i['server_id'],gpu_id=i['gpu_id'],match=i['deployment']['flow']['match'])
                          for i in bundle['instances']])
        self._active_context=context
        self._senders_armed=False
        self._host_trace=[]
        if case['name']=='prepare_rollback':context['prepare_failure']=item['prepare_failure']
        self.controller=self._open(directory/'controller.sqlite')
        dispatched=recover
        fixture_used=recover
        try:
            if recover:
                # A pre-effect intent may already have acted. Only query/cleanup.
                status=self._reconcile()
                self.evidence['final_status']=status
            else:
                reason=self._topology(case['name'],bundle)
                if self.fixture is None:reason=reason or 'fixed acceptance peer not configured'
                if reason:
                    self.evidence.update(TEST_RESULT='UNSUPPORTED',unsupported_reason=reason)
                    return
                checked=self._fixture('check',context)
                if checked.get('supported') is not True:
                    self.evidence.update(TEST_RESULT='UNSUPPORTED',unsupported_reason=checked.get('reason'))
                    return
                if case['name']=='prepare_rollback':
                    negative=checked.get('negative_fixtures',[])
                    if len(negative)!=1:
                        raise AcceptanceFailure('HARNESS_FAIL','preinstalled negative fixture identity missing')
                    self.evidence['negative_fixture']=negative[0]
                if case['name']=='runtime_failure':
                    fault=self._configured_fault(checked.get('fault_targets',[]),bundle,document,item['run_id'])
                    self.evidence['configured_fault']=fault
                    context['target']=[fault['binding']['server_id'],fault['binding']['gpu_id']]
                self.controller.register_run(item['run_id'],bundle,document)
                self._event('registered',bundle_id=bundle['bundle_id'])
                dispatched=True
                fixture_used=True
                status=self._request('prepare')
                if case['name']=='prepare_rollback':
                    status=self._until(lambda s:s['phase']=='FINISHED',self.suite['case_timeout_seconds'])
                    if status['result']!='FAILED':raise AcceptanceFailure('PRODUCT_FAIL','prepare rollback did not fail')
                    ready=self._verify_rollback_history(item['prepare_failure'])
                else:
                    status=self._until(lambda s:s['phase']=='READY',self.suite['case_timeout_seconds'])
                    if not all(i['state']=='READY' and i['fresh'] for i in status['instances']):
                        raise ValueError('all-ready barrier absent')
                    self._fixture('arm',context)
                    self._senders_armed=True
                    self._sender_health()
                    status=self._request('start')
                    if case['name']=='disconnect_reconnect':
                        self._until(lambda s:all(i['state']=='RUNNING' for i in s['instances']),
                                    self.suite['case_timeout_seconds'])
                        self.controller.close()
                        self._event('controller_disconnected')
                        self.sleep(self.suite['poll_interval_seconds'])
                        self.controller=self._open(directory/'controller.sqlite')
                        status=self._reconcile()
                    if case['name']=='runtime_failure':
                        status=self._until(lambda s:all(i['state']=='RUNNING' for i in s['instances']),
                                           self.suite['case_timeout_seconds'])
                        self._fixture('inject',context)
                        status=self._until(lambda s:self._runtime_failed_target(s,fault),
                            self.suite['case_timeout_seconds'])
                        self._event('healthy_peer_continues',status=status)
                    if case['name'] in ('manual_graceful','manual_force'):
                        self._until(lambda s:all(i['state']=='RUNNING' for i in s['instances']),
                                    self.suite['case_timeout_seconds'])
                        self._request('stop','graceful' if case['name']=='manual_graceful' else 'force')
                    status=self._until(lambda s:s['phase']=='FINISHED',self.suite['case_timeout_seconds'])
                    wanted=('INTERRUPTED' if case['name'].startswith('manual_') else
                            'PARTIAL_FAILED' if case['name']=='runtime_failure' else 'PASS')
                    if status['result']!=wanted:raise AcceptanceFailure('PRODUCT_FAIL','unexpected run result '+str(status['result']))
                self.evidence['final_status']=status
                collection=self._fixture('collect',context)
                self.evidence['collection']=collection
                if case['name']=='prepare_rollback' and collection.get('negative_fixtures')!=[self.evidence['negative_fixture']]:
                    raise AcceptanceFailure('HARNESS_FAIL','negative fixture identity changed after preflight')
                self._verify_collection(collection,status,case['name'])
                if case['name']=='prepare_rollback':self._verify_rollback_resources(ready,collection)
                if case['name']=='runtime_failure':self._verify_runtime_fault(fault,collection,status)
                self.evidence['TEST_RESULT']='PASS'
        except Exception as error:
            self._failure(error,'functional')
            self.evidence['TEST_RESULT']=failure_result(self.evidence['FAILURE_CLASSIFICATION'])
        finally:
            self._senders_armed=False
            # BaseException deliberately leaves append-only intent for --resume;
            # never claim cleanup after an interrupted controller process.
            if sys.exc_info()[0] is None:
                if dispatched:
                    try:
                        status=self._reconcile()
                        if status['phase']!='FINISHED':
                            self._request('stop','force')
                            status=self._until(lambda s:s['phase']=='FINISHED',self.suite['cleanup_timeout_seconds'])
                        self.evidence['final_status']=status
                        if any(i['possibly_owned'] or i['cleanup'] not in ('CONFIRMED','NOT_REQUIRED') for i in status['instances']):
                            raise ValueError('pipeline ownership cleanup unresolved')
                        self.evidence['CLEANUP_RESULT']='PASS'
                    except Exception as error:
                        self._failure(error,'cleanup')
                        self.evidence['CLEANUP_RESULT']='FAIL'
                    if fixture_used:
                        try:
                            cleaned=self._fixture('cleanup',context)
                            self.evidence['cleanup_evidence']=cleaned
                            flags=('processes_gone','descendants_gone','resources_gone','sender_processes_gone')
                            if any(cleaned.get(k) is not True for k in flags):
                                raise ValueError('exact owned fixture/supervisor cleanup unresolved')
                        except Exception as error:
                            self._failure(error,'fixture_cleanup')
                            self.evidence['CLEANUP_RESULT']='FAIL'
                self._event('finished',result=self.evidence)
                _write_new(directory/'result.json',self.evidence)
            self.controller.close()

    def _preflight(self):
        # Validate every immutable binding before the first remote allocation.
        with tempfile.TemporaryDirectory(prefix='ms07-acceptance-bindings-') as scratch:
            controller=self._open(Path(scratch).resolve()/'validation.sqlite')
            try:
                for case in self.suite['cases']:
                    for item in case['repetitions']:
                        saved=self.output/item['run_id']/'inputs.json'
                        if self.resume and saved.exists():inputs=read_json(saved)
                        else:
                            bundle_path=Path(item['bundle_path'])/'bundle.json'
                            inputs=dict(bundle=read_json(bundle_path),global_document=read_json(item['global_path']),
                                input_identities=dict(bundle_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
                                    bundle_manifest_sha256=hashlib.sha256((Path(item['bundle_path'])/'MANIFEST.sha256').read_bytes()).hexdigest(),
                                    global_sha256=hashlib.sha256(Path(item['global_path']).read_bytes()).hexdigest()))
                        controller.register_run(item['run_id'],inputs['bundle'],inputs['global_document'])
                        if case['name']=='prepare_rollback':
                            target=(item['prepare_failure']['server_id'],item['prepare_failure']['gpu_id'])
                            order=[(i['server_id'],i['gpu_id']) for i in inputs['bundle']['instances']]
                            if target not in order or order.index(target)==0:
                                raise ValueError('prepare failure target must be installed and follow a healthy participant')
                        self.inputs[item['run_id']]=inputs
            finally:controller.close()

    def run(self):
        self.output.mkdir(mode=0o700,parents=True,exist_ok=True)
        lock_path=self.output/'campaign.lock'
        with lock_path.open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            identity_path=self.output/'suite.json'
            if identity_path.exists():
                if not self.resume:raise ValueError('campaign exists; explicit --resume required')
                if read_json(identity_path)!=self.suite:raise ValueError('campaign immutable suite mismatch')
            else:
                if self.resume:raise ValueError('cannot resume nonexistent campaign')
                _write_new(identity_path,self.suite)
            self._preflight()
            if self.adapter is None:
                self.adapter=SSHHostAdapter(self.suite['endpoints'],ssh_path=self.suite['ssh_path'],
                                           timeout_seconds=self.suite['transport_timeout_seconds'])
            if self.fixture is None and self.suite.get('fixture_endpoints'):
                self.fixture=PeerFixture(self.suite)
            results=[];resumed_incomplete=False
            for case in self.suite['cases']:
                for item in case['repetitions']:
                    directory=self.output/item['run_id']
                    path=directory/'result.json'
                    if path.exists():
                        result=read_json(path)
                    elif resumed_incomplete:
                        continue
                    else:
                        existed=directory.exists()
                        directory.mkdir(mode=0o700,exist_ok=True)
                        self._one(case,item,directory,existed)
                        result=read_json(path)
                        resumed_incomplete=existed
                    result=dict(result,evidence_path=str(path.relative_to(self.output)),
                                evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                    results.append(result)
                    if result['CLEANUP_RESULT']=='FAIL':break
                if results and results[-1]['CLEANUP_RESULT']=='FAIL':break
            retirement=dict(host_supervisors_gone=True,state='NOT_REQUIRED')
            if self.fixture is not None and any(r['CLEANUP_RESULT']!='NOT_REQUIRED' for r in results):
                try:
                    context=dict(identity=self.suite['identity'],suite_id=self.suite['suite_id'],
                                 run_id=results[-1]['run_id'],case=results[-1]['case'])
                    retirement=self.fixture.perform('retire',context)
                    if retirement.get('host_supervisors_gone') is not True:
                        raise ValueError('batch host supervisor retirement unresolved')
                except Exception as error:
                    retirement=dict(host_supervisors_gone=False,error=str(error))
            full={c['name'] for c in self.suite['cases']}==set(CASES) and len(results)==30
            failures=[failure for result in results for failure in result.get('failures',[])]
            if not retirement['host_supervisors_gone']:
                failures.append(dict(stage='retirement',classification='HARNESS_FAIL',detail=retirement.get('error')))
            counts={kind:sum(f.get('classification','HARNESS_FAIL')==kind for f in failures) for kind in FAILURE_CLASSES}
            first=next((r['first_failure'] for r in results if r['first_failure'] is not None),None)
            if first is None and failures:first=failures[0]
            outcome=(failure_result(first.get('classification','HARNESS_FAIL')) if first else
                     'PASS' if full and all(r['TEST_RESULT']=='PASS' for r in results) else 'INCOMPLETE')
            report=dict(schema_version=1,suite_id=self.suite['suite_id'],identity=self.suite['identity'],
                        scope='FUNCTIONAL_NON_SATURATION',FAILURE_COUNTS=counts,first_failure=first,
                        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        TEST_RESULT=outcome,CLEANUP_RESULT='FAIL' if not retirement['host_supervisors_gone'] or any(r['CLEANUP_RESULT']=='FAIL' for r in results) else 'PASS',
                        retirement=retirement,
                        RESULT_NOTIFICATION='NOT_ATTEMPTED',status='REVIEW_PENDING',runs=results)
            # Reports are immutable per invocation; original case evidence remains.
            index=1
            while (self.output/('summary-{}.json'.format(index))).exists():index+=1
            _write_new(self.output/('summary-{}.json'.format(index)),report)
            return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    try:
        result=AcceptanceRunner(args.suite,args.output,resume=args.resume).run()
        print(json.dumps(result,sort_keys=True,allow_nan=False))
        return 0 if result['TEST_RESULT']=='PASS' else 2
    except Exception as error:
        print(json.dumps(dict(TEST_RESULT='HARNESS_FAIL',error=str(error)),sort_keys=True))
        return 1


if __name__=='__main__':
    sys.exit(main())
