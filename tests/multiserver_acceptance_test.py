"""Acceptance runner tests: real MS06 store/controller, host policy and transport.

Only hardware/runtime and the SSH executable boundary are substituted. None of
these local tests represents sender traffic, CUDA, SysV IPC or remote acceptance.
"""
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from multiserver_controller_test import FakeClock, FakeHosts
from multiserver_fixture import write_example_inputs
from scripts.multiserver.bundle import build_bundle

try:
    from scripts.multiserver_acceptance import AcceptanceRunner, load_suite
except ImportError:
    AcceptanceRunner = load_suite = None


class Fixture:
    """Explicit dataflow double. Real runs require a separately pinned fixture."""
    def __init__(self, hosts):
        self.hosts = hosts
        self.events = []
        self.unsupported = False
        self.bad_cleanup = False
        self.bad_counts = False
        self.fail_arm = False
        self.complete_on_collect = False
        self.fault_target=next(iter(hosts.hosts))

    def fault_plan(self,context):
        key=self.fault_target
        instance=next(i for i in self.hosts.bundle['instances'] if (i['server_id'],i['gpu_id'])==key)
        return dict(binding=dict(run_id=context['run_id'],bundle_id=self.hosts.bundle['bundle_id'],
            server_id=key[0],gpu_id=key[1],observation_sha256=instance['observation_sha256']),
            role='gpu',signal='SIGKILL',after_running_seconds=.01,deployment_sha256='d'*64)

    def perform(self, operation, context):
        self.events.append(operation)
        if operation == 'retire':
            return dict(identity=context['identity'],host_supervisors_gone=True)
        if operation == 'check':
            return dict(supported=not self.unsupported,
                        reason='sender fixture absent' if self.unsupported else None,
                        identity=context['identity'],negative_fixtures=[{'declared':'test double'}]
                        if context['case']=='prepare_rollback' else [],
                        fault_targets=[self.fault_plan(context)] if context['case']=='runtime_failure' else [])
        if operation == 'arm' and self.fail_arm:
            raise OSError('sender launch reply lost')
        if operation == 'health':
            return dict(identity=context['identity'],healthy=True,senders=[])
        if operation == 'inject':
            key = tuple(context['target'])
            self.hosts.set_state(key, 'FAILED', error='injected owned worker exit')
        if operation == 'collect':
            items=[]
            for key,host in self.hosts.hosts.items():
                if host['state']=='COMPLETED':
                    items.append(dict(server_id=key[0],gpu_id=key[1],
                                      sender_packets=10,receiver_packets=10,
                                      unpack_packets=9 if self.bad_counts else 10,
                                      input_bytes=128,output_bytes=64,unpack_output_bytes=128,expected_input_bytes=128,
                                      expected_output_bytes=64,expected_packets=10,published_blocks=1,expected_blocks=1,eod=True))
            launches=[dict(server_id=k[0],gpu_id=k[1],generation=h['generation'],
                                       prepare_count=1,start_count=1)
                                  for k,h in self.hosts.hosts.items() if h['generation']]
            result=dict(identity=context['identity'], instances=items,launches=launches,
                        artifacts=[dict(path='/feature/evidence/raw.json',sha256='a'*64)])
            if context['case']=='runtime_failure':
                plan=self.fault_plan(context);key=self.fault_target
                generation=self.hosts.hosts[key]['generation'];owner=dict(plan['binding'],generation=generation)
                process=dict(pid=102,start_ticks=123,boot_id='fixture-boot',uid=os.getuid(),pgid=102,sid=102)
                identity=dict(process,owner=owner,role='gpu')
                proof=dict(owner=owner,identity=identity,role='gpu',signal='SIGKILL')
                artifact=dict(path='/feature/evidence/fault.json',sha256='f'*64)
                row=next(r for r in launches if (r['server_id'],r['gpu_id'])==key)
                row.update(claim=dict(owner=owner,resources={},released=True,cleanup_evidence=dict(
                    owner=owner,resources={},processes_gone=True,descendants_gone=True,resources_gone=True)),
                    processes=[dict(owner=owner,role='gpu',state='EXITED',returncode=-9,process=process)])
                result.update(fault_targets=[plan],fault_records=[dict(server_id=key[0],gpu_id=key[1],
                    generation=generation,proof=proof,artifact=artifact)])
                result['artifacts'].append(artifact)
            return result
        if operation == 'cleanup':
            return dict(identity=context['identity'],processes_gone=not self.bad_cleanup,
                        resources_gone=True,descendants_gone=True,
                        host_supervisors_gone=True,sender_processes_gone=True,
                        artifacts=[dict(path='/feature/evidence/cleanup.json',sha256='b'*64)])
        return dict(identity=context['identity'],applied=True)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ms-i1-inputs-')
        cls.root=Path(cls.tmp.name).resolve()
        global_path, profiles, inventory=write_example_inputs(cls.root/'inputs')
        cls.document=json.loads(global_path.read_text())
        cls.bundle=build_bundle(global_path,profiles,cls.root/'bundle',inventory_path=inventory)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.assertIsNotNone(AcceptanceRunner,'versioned acceptance runner missing')
        self.tmp_run=tempfile.TemporaryDirectory(prefix='ms-i1-results-')
        self.addCleanup(self.tmp_run.cleanup)
        self.output=Path(self.tmp_run.name).resolve()/'campaign'
        self.clock=FakeClock()
        self.hosts=FakeHosts(self.bundle)
        self.fixture=Fixture(self.hosts)
        self.sleeps=0

    def suite(self,case='finite_eod'):
        suite=dict(schema_version=1,suite_id='functional-fixture',
                    identity=dict(source_revision='2b746a4',source_manifest_sha256='c'*64,
                                  build_id='fixture-build'),
                    endpoints={'node-a':{},'node-b':{}},
                    ssh_path='/usr/bin/ssh',transport_timeout_seconds=1,
                    poll_interval_seconds=0.01,case_timeout_seconds=1,cleanup_timeout_seconds=1,
                    cases=[dict(name=case,repetitions=[dict(run_id='run-{}'.format(n),
                        bundle_path=str(self.root/'bundle'),global_path=str(self.root/'bundle/global-observation.json'))
                        for n in range(1,4)])])
        if case=='prepare_rollback':
            for item in suite['cases'][0]['repetitions']:
                item['prepare_failure']=dict(server_id='node-b',gpu_id=0,expected_error='BUNDLE_MANIFEST')
        return suite

    def sleep(self,seconds):
        self.sleeps+=1
        self.clock.tick+=int(seconds*1e9)
        for key,host in self.hosts.hosts.items():
            if host['state']=='RUNNING':
                self.hosts.set_state(key,'COMPLETED',cleanup='CONFIRMED',evidence=dict(
                    target_data_complete=True,output_published=True,eod_complete=True))

    def runner(self,suite=None,**extra):
        return AcceptanceRunner(suite or self.suite(),self.output,host_adapter=self.hosts,
            fixture=self.fixture,utc_now=self.clock.utc_now,monotonic_ns=self.clock.monotonic_ns,
            sleep=self.sleep,**extra)

    def test_three_independent_lifecycles_save_counts_and_cleanup(self):
        report=self.runner().run()
        self.assertEqual([r['TEST_RESULT'] for r in report['runs']],['PASS']*3)
        self.assertEqual([r['CLEANUP_RESULT'] for r in report['runs']],['PASS']*3)
        self.assertEqual(report['TEST_RESULT'],'INCOMPLETE')  # subset is diagnostic
        self.assertEqual(len([c for _,c in self.hosts.calls if c['operation']=='prepare']),6)
        for result in report['runs']:
            saved=json.loads((self.output/result['evidence_path']).read_text())
            self.assertEqual(saved['run_id'],result['run_id'])
            self.assertIsNone(saved['first_failure'])
            self.assertEqual(len(saved['collection']['instances']),2)

    def test_prepare_rejection_without_exact_reason_and_allocation_evidence_cannot_pass(self):
        self.hosts.reject.add((('node-b',0),'prepare'))
        report=self.runner(self.suite('prepare_rollback')).run()
        self.assertTrue(all(r['TEST_RESULT']!='PASS' for r in report['runs']))
        self.assertFalse(any(c['operation']=='start' for _,c in self.hosts.calls))
        self.assertTrue(any(c['operation']=='stop' for _,c in self.hosts.calls))

    def test_prepare_failure_target_cannot_be_first_or_foreign(self):
        for index,target in enumerate([('node-a',0),('foreign',1)]):
            self.output=self.output.parent/('invalid-target-'+str(index))
            suite=self.suite('prepare_rollback')
            for item in suite['cases'][0]['repetitions']:
                item['prepare_failure'].update(server_id=target[0],gpu_id=target[1])
            with self.subTest(target=target),self.assertRaises(ValueError):self.runner(suite).run()
        self.assertEqual(self.hosts.calls,[])

    def test_both_initial_prepare_rejections_with_no_stop_never_pass(self):
        self.hosts.reject.update({(('node-a',0),'prepare'),(('node-b',0),'prepare')})
        report=self.runner(self.suite('prepare_rollback')).run()
        self.assertTrue(all(r['TEST_RESULT']!='PASS' for r in report['runs']))
        self.assertFalse(any(c['operation']=='stop' for _,c in self.hosts.calls))

    def test_manual_stop_targets_whole_observation_and_retains_interruption(self):
        for case,mode in [('manual_graceful','graceful'),('manual_force','force')]:
            with self.subTest(case=case):
                self.hosts=FakeHosts(self.bundle)
                self.fixture=Fixture(self.hosts)
                self.output=self.output.parent/case
                report=self.runner(self.suite(case)).run()
                self.assertEqual([r['TEST_RESULT'] for r in report['runs']],['PASS']*3)
                self.assertEqual(report['runs'][0]['final_status']['result'],'INTERRUPTED')
                stops=[c for _,c in self.hosts.calls if c['operation']=='stop']
                self.assertTrue(any(c['mode']==mode for c in stops))

    def test_cleanup_failure_never_rewrites_functional_pass(self):
        self.fixture.bad_cleanup=True
        report=self.runner().run()
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'PASS')
        self.assertEqual(report['runs'][0]['CLEANUP_RESULT'],'FAIL')
        self.assertEqual(report['TEST_RESULT'],'HARNESS_FAIL')
        self.assertEqual(len(report['runs']),1)  # no new dataflow while residue uncertain

    def test_count_mismatch_is_failed_even_with_completed_host_booleans(self):
        self.fixture.bad_counts=True
        report=self.runner().run()
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'FAIL')
        self.assertEqual(report['runs'][0]['FAILURE_CLASSIFICATION'],'PRODUCT_FAIL')
        self.assertIn('count',report['runs'][0]['first_failure']['detail'])
        self.assertEqual(report['runs'][0]['CLEANUP_RESULT'],'PASS')

    def test_gpu_byte_mismatch_cannot_pass_with_host_completion_flags(self):
        original=self.fixture.perform
        def mismatch(operation,context):
            result=original(operation,context)
            if operation=='collect':result['instances'][0]['output_bytes']=63
            return result
        self.fixture.perform=mismatch
        report=self.runner().run()
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'FAIL')

    def test_missing_fixture_capability_is_unsupported_and_dispatches_nothing(self):
        self.fixture.unsupported=True
        report=self.runner().run()
        self.assertEqual([r['TEST_RESULT'] for r in report['runs']],['UNSUPPORTED']*3)
        self.assertEqual(self.hosts.calls,[])
        self.assertEqual(report['TEST_RESULT'],'INCOMPLETE')

    def test_sender_uncertainty_records_first_failure_and_cleans_owned_pipelines(self):
        self.fixture.fail_arm=True
        report=self.runner().run()
        result=report['runs'][0]
        self.assertEqual(result['TEST_RESULT'],'HARNESS_FAIL')
        self.assertEqual(result['FAILURE_CLASSIFICATION'],'HARNESS_FAIL')
        self.assertEqual(result['first_failure']['detail'],'sender launch reply lost')
        self.assertEqual(result['CLEANUP_RESULT'],'PASS')
        self.assertFalse(any(c['operation']=='start' for _,c in self.hosts.calls))
        self.assertIn('cleanup',self.fixture.events)

    def test_missed_immutable_utc_is_environment_blocked_not_product_failure(self):
        self.clock.utc=datetime.datetime(2026,9,12,0,1,tzinfo=datetime.timezone.utc)
        report=self.runner().run()
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'ENV_BLOCKED')
        self.assertEqual(report['TEST_RESULT'],'ENV_BLOCKED')
        self.assertEqual(report['FAILURE_COUNTS']['ENV_BLOCKED'],3)
        self.assertFalse(any(c['operation']=='start' for _,c in self.hosts.calls))

    def test_fixture_identity_error_is_harness_failure(self):
        original=self.fixture.perform
        def wrong(operation,context):
            result=original(operation,context)
            if operation=='check':result['identity']={'source':'foreign'}
            return result
        self.fixture.perform=wrong
        report=self.runner().run()
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'HARNESS_FAIL')
        self.assertEqual(report['TEST_RESULT'],'HARNESS_FAIL')
        self.assertEqual(self.hosts.calls,[])

    def test_missing_sender_health_observation_is_harness_not_input_failure(self):
        original=self.fixture.perform
        def missing(operation,context):
            if operation=='health':return dict(identity=context['identity'],healthy=False,senders=[])
            return original(operation,context)
        self.fixture.perform=missing
        report=self.runner().run()
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'HARNESS_FAIL')

    def test_cleanup_error_does_not_reclassify_first_product_failure(self):
        self.fixture.bad_counts=True;self.fixture.bad_cleanup=True
        report=self.runner().run();case=report['runs'][0]
        self.assertEqual(case['first_failure']['classification'],'PRODUCT_FAIL')
        self.assertEqual(case['FAILURE_CLASSIFICATION'],'PRODUCT_FAIL')
        self.assertEqual(report['FAILURE_COUNTS']['PRODUCT_FAIL'],1)
        self.assertEqual(report['FAILURE_COUNTS']['HARNESS_FAIL'],1)

    def test_real_peer_sender_health_failure_stops_whole_transfer_before_collection(self):
        from scripts.multiserver_acceptance import PeerFixture
        from multiserver_pipeline_runtime_test import FakeSupervisor
        import importlib.util
        path=Path(__file__).parent/'fixtures/multiserver_acceptance_peer.py'
        spec=importlib.util.spec_from_file_location('health_peer',str(path))
        peer=importlib.util.module_from_spec(spec);spec.loader.exec_module(peer)
        root=self.output.parent/'sender';root.mkdir(mode=0o700)
        binary=root/'fpga_sender_sim';binary.write_text('#!/usr/bin/python3\n');binary.chmod(0o700)
        config=dict(schema_version=1,role='sender',server_id='sender',root=str(root/'state'),
            identity=self.suite()['identity'],lifetime_seconds=10,
            binary=dict(path=str(binary),sha256=peer.sha(binary),leaf=True),jobs=[])
        Path(config['root']).mkdir(mode=0o700)
        utc=datetime.datetime.strptime(self.document['observation']['utc_start'],'%Y-%m-%d-%H:%M:%S')
        epoch=datetime.datetime(utc.year,7,1)
        for repetition in range(1,4):
            for index,instance in enumerate(self.bundle['instances']):
                assignment=instance['assignment'];match=instance['deployment']['flow']['match']
                source=dict(schema_version=3,source=dict(ip='127.0.0.1',port=41000+index),
                    destination=dict(ip=match['destination_ip'],port=match['destination_port'],path_mtu=9000),
                    station=dict(station_ids=self.document['observation']['station_ids']),
                    packet=dict(first_channel_id=assignment['first_channel_id'],nchan=assignment['nchan'],
                        npol=1,nsamp_per_packet=512,component_bits=8,sample_interval_ps='1000000'),
                    time=dict(reference_epoch=53,start_seconds=int((utc-epoch).total_seconds()),
                        group_count=2048,mode='PACED',start_utc=self.document['observation']['utc_start']),
                    transmit=dict(target_gbps=1,batch_packets=16,payload_mode='REPEAT_TEMPLATE'),
                    faults=dict(drop_groups=[],duplicate_groups=[],invalid_header_groups=[]))
                cfg=root/('config-{}-{}.json'.format(repetition,index));cfg.write_text(json.dumps(source))
                config['jobs'].append(dict(run_id='run-'+str(repetition),bundle_id=self.bundle['bundle_id'],
                    server_id=instance['server_id'],gpu_id=instance['gpu_id'],observation_sha256=instance['observation_sha256'],
                    config_path=str(cfg),config_sha256=peer.sha(cfg)))
        sessions={};observed=[];collected=[]
        hosts=self.hosts
        class LocalPeerAdapter:
            def send(inner,key,command):
                context=command['context'];operation=command['operation'];run=context['run_id']
                if key[0]=='receiver':
                    if operation=='check':
                        reply=dict(supported=True,coverage=[],receiver_instances=[list(k) for k in hosts.hosts])
                    elif operation=='health':reply=dict(senders=[])
                    elif operation in ('cleanup','retire'):
                        reply=dict(processes_gone=True,resources_gone=True,descendants_gone=True,
                            host_supervisors_gone=True,sender_processes_gone=True,artifacts=[])
                    else:reply=dict(applied=True)
                    role='receiver'
                else:
                    role='sender'
                    if operation=='arm':
                        # Substitute only the Linux process-launch boundary.
                        supervisor=FakeSupervisor();session=peer.SenderSession(config,context,supervisor);session.arm()
                        supervisor.reports[(session.reports[0]['identity']['owner']['generation'],'sender-0')].update(
                            state='EXITED',returncode=7)
                        sessions[run]=session
                        private=peer.PrivateRoot(peer.sender_root(config,context))
                        try:private.write('status.json',dict(phase='RUNNING',**session.collect()))
                        finally:private.close()
                        reply=dict(applied=True)
                    elif operation=='cleanup':
                        session=sessions[run];cleanup=session.cleanup()
                        private=peer.PrivateRoot(peer.sender_root(config,context))
                        try:private.write('status.json',dict(phase='EXITED',cleanup=cleanup,**session.collect()))
                        finally:private.close()
                        reply=peer.sender_exchange(config,context,operation,'/unused/private-config')
                    elif operation=='collect':
                        collected.append(run);reply=peer.sender_exchange(config,context,operation,'/unused/private-config')
                    else:
                        reply=peer.sender_exchange(config,context,operation,'/unused/private-config')
                        if operation=='health':observed.append(reply)
                return dict(reply,identity=context['identity'],run_id=run,role=role)
        fixture=PeerFixture.__new__(PeerFixture);fixture.peers=['sender','receiver'];fixture.adapter=LocalPeerAdapter()
        runner=self.runner();runner.fixture=fixture
        report=runner.run()
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'INPUT_FAIL')
        self.assertEqual(report['TEST_RESULT'],'INPUT_FAIL')
        self.assertTrue(observed)
        self.assertEqual([s['state'] for s in observed[0]['senders']],['EXITED','RUNNING'])
        self.assertEqual(collected,[])
        stopped={key for key,cmd in self.hosts.calls if cmd['operation']=='stop'}
        self.assertEqual(stopped,{('node-a',0),('node-b',0)})
        self.assertTrue(all(s['state']=='EXITED' for s in sessions['run-1'].collect()['senders']))

    def test_repeat_run_does_not_dispatch_or_rewrite_original_evidence(self):
        first=self.runner().run()
        before=(self.output/first['runs'][0]['evidence_path']).read_bytes()
        calls=len(self.hosts.calls)
        second=self.runner(resume=True).run()
        self.assertEqual(second['runs'],first['runs'])
        self.assertEqual(len(self.hosts.calls),calls)
        self.assertEqual((self.output/first['runs'][0]['evidence_path']).read_bytes(),before)

    def test_resume_after_lost_sender_reply_queries_and_cleans_without_replay(self):
        original=self.fixture.perform
        def crash(operation,context):
            if operation=='arm':
                raise SystemExit('controller process crash')
            return original(operation,context)
        self.fixture.perform=crash
        with self.assertRaises(SystemExit):
            self.runner().run()
        before=len([c for _,c in self.hosts.calls if c['operation'] in ('prepare','start')])
        self.fixture.perform=original
        report=self.runner(resume=True).run()
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'INCOMPLETE')
        self.assertEqual(report['runs'][0]['CLEANUP_RESULT'],'PASS')
        self.assertEqual(len([c for _,c in self.hosts.calls if c['operation'] in ('prepare','start')]),before)
        self.assertEqual(self.fixture.events.count('arm'),0)

    def test_single_and_samehost_channel_splits_execute_three_clean_repetitions(self):
        from multiserver_deployment_test import use_second_gpu_on_node_a
        from multiserver_fixture import write_weight_fixture
        for name in ('single','samehost_equal','samehost_extra'):
            with self.subTest(case=name):
                parent=Path(self.tmp_run.name).resolve()/name
                global_path,deployments_path,inventory=write_example_inputs(parent/'inputs')
                document=json.loads(global_path.read_text());deployments=json.loads(deployments_path.read_text())
                if name=='single':
                    document['assignments']=document['assignments'][:1]
                    document['assignments'][0]['nchan']=7
                else:
                    use_second_gpu_on_node_a(deployments,document)
                    if name=='samehost_equal':
                        document['channels']['nchan']=8;document['assignments'][1]['nchan']=4
                        write_weight_fixture(global_path.parent/'weights/global-f7-p1-a4-b2.npy',(8,1,4,2,2),'|i1')
                global_path.write_text(json.dumps(document));deployments_path.write_text(json.dumps(deployments))
                bundle=build_bundle(global_path,deployments_path,parent/'bundle',inventory_path=inventory)
                self.hosts=FakeHosts(bundle);self.fixture=Fixture(self.hosts)
                suite=self.suite(name)
                for item in suite['cases'][0]['repetitions']:
                    item.update(bundle_path=str(parent/'bundle'),global_path=str(parent/'bundle/global-observation.json'))
                self.output=parent/'results'
                with patch('multiserver_controller_test.KEYS',list(self.hosts.hosts)):
                    result=self.runner(suite).run()
                self.assertEqual([r['TEST_RESULT'] for r in result['runs']],['PASS']*3)

    def test_peer_cleanup_attempts_other_owners_after_one_transport_failure(self):
        from scripts.multiserver_acceptance import PeerFixture
        class Adapter:
            def __init__(self):self.owned={'a','b'}
            def send(self,key,command):
                if key[0]=='a':raise OSError('unreachable first peer')
                self.owned.remove('b')
                return dict(identity=command['context']['identity'],run_id='run',role='sender',
                    processes_gone=True,resources_gone=True,descendants_gone=True,
                    host_supervisors_gone=True,sender_processes_gone=True)
        fixture=PeerFixture.__new__(PeerFixture);fixture.peers=['a','b'];fixture.adapter=Adapter()
        result=fixture.perform('cleanup',dict(identity={'source':'fixed'},run_id='run'))
        self.assertFalse(result['sender_processes_gone'])
        self.assertEqual(fixture.adapter.owned,{'a'})
        self.assertEqual(result['failures'][0]['peer'],'a')

    def test_all_ready_barrier_precedes_first_start_in_each_repetition(self):
        report=self.runner(self.suite('all_ready')).run()
        self.assertEqual([r['TEST_RESULT'] for r in report['runs']],['PASS']*3)
        self.assertEqual(self.hosts.before_start_states[::2],[['READY','READY']]*3)

    def test_disconnect_reopens_controller_without_duplicate_prepare_or_start(self):
        report=self.runner(self.suite('disconnect_reconnect')).run()
        self.assertEqual([r['TEST_RESULT'] for r in report['runs']],['PASS']*3)
        self.assertEqual(len([c for _,c in self.hosts.calls if c['operation']=='prepare']),6)
        self.assertEqual(len([c for _,c in self.hosts.calls if c['operation']=='start']),6)

    def test_runtime_failure_leaves_healthy_peer_running_then_completed(self):
        report=self.runner(self.suite('runtime_failure')).run()
        self.assertEqual([r['TEST_RESULT'] for r in report['runs']],['PASS']*3)
        self.assertEqual(report['runs'][0]['final_status']['result'],'PARTIAL_FAILED')
        stops=[key for key,c in self.hosts.calls if c['operation']=='stop']
        self.assertTrue(stops)
        self.assertEqual(set(stops),{('node-a',0)})

    def test_runtime_failure_of_wrong_instance_cannot_pass(self):
        original=self.fixture.perform
        def wrong(operation,context):
            if operation=='inject':
                self.hosts.set_state(('node-b',0),'FAILED',error='unrelated GPU failure')
                return dict(identity=context['identity'],applied=True)
            return original(operation,context)
        self.fixture.perform=wrong
        report=self.runner(self.suite('runtime_failure')).run()
        self.assertTrue(all(r['TEST_RESULT']!='PASS' for r in report['runs']))
        self.assertGreater(sum(report['FAILURE_COUNTS'].values()),0)

    def test_runtime_failure_uses_preinstalled_target_instead_of_first_instance(self):
        self.fixture.fault_target=('node-b',0)
        report=self.runner(self.suite('runtime_failure')).run()
        self.assertEqual([r['TEST_RESULT'] for r in report['runs']],['PASS']*3)
        self.assertEqual(report['runs'][0]['final_status']['failed_instances'],[dict(server_id='node-b',gpu_id=0)])

    def test_runtime_failure_missing_or_mismatched_fixed_proof_cannot_pass(self):
        variants=('missing','owner','generation','role','signal','pid','start','boot','ledger-exit','ledger-owner','duplicate-gpu','plan')
        for variant in variants:
            self.output=self.output.parent/('fault-proof-'+variant)
            self.hosts=FakeHosts(self.bundle);self.fixture=Fixture(self.hosts)
            original=self.fixture.perform
            def tampered(operation,context):
                result=original(operation,context)
                if operation!='collect':return result
                if variant=='missing':result['fault_records']=[];return result
                record=result['fault_records'][0];proof=record['proof']
                if variant=='owner':proof['owner']=dict(proof['owner'],run_id='foreign')
                elif variant=='generation':record['generation']='foreign'
                elif variant=='role':proof['role']='receiver'
                elif variant=='signal':proof['signal']='SIGTERM'
                elif variant=='pid':proof['identity']=dict(proof['identity'],pid=999)
                elif variant=='start':proof['identity']=dict(proof['identity'],start_ticks=999)
                elif variant=='boot':proof['identity']=dict(proof['identity'],boot_id='foreign')
                elif variant=='plan':result['fault_targets'][0]['binding']['server_id']='node-b'
                else:
                    row=next(r for r in result['launches'] if r['server_id']=='node-a')
                    if variant=='ledger-exit':row['processes'][0]['returncode']=7
                    elif variant=='ledger-owner':row['processes'][0]['owner']=dict(row['processes'][0]['owner'],generation='foreign')
                    else:row['processes'].append(copy.deepcopy(row['processes'][0]))
                return result
            self.fixture.perform=tampered
            with self.subTest(variant=variant):
                report=self.runner(self.suite('runtime_failure')).run()
                self.assertTrue(all(r['TEST_RESULT']!='PASS' for r in report['runs']))

    def test_runtime_failure_requires_one_valid_preinstalled_binding_before_dispatch(self):
        for variant in ('missing','duplicate','foreign','boolean-gpu'):
            self.output=self.output.parent/('fault-plan-'+variant)
            self.hosts=FakeHosts(self.bundle);self.fixture=Fixture(self.hosts)
            original=self.fixture.perform
            def invalid(operation,context):
                result=original(operation,context)
                if operation=='check':
                    if variant=='missing':result['fault_targets']=[]
                    elif variant=='duplicate':result['fault_targets']*=2
                    elif variant=='foreign':result['fault_targets'][0]['binding']['server_id']='foreign'
                    else:result['fault_targets'][0]['binding']['gpu_id']=False
                return result
            self.fixture.perform=invalid
            with self.subTest(variant=variant):
                report=self.runner(self.suite('runtime_failure')).run()
                self.assertEqual([r['TEST_RESULT'] for r in report['runs']],['HARNESS_FAIL']*3)
                self.assertEqual(self.hosts.calls,[])

    def test_failure_before_cleanup_crash_survives_resume(self):
        self.fixture.fail_arm=True
        original=self.fixture.perform
        def crash(operation,context):
            if operation=='cleanup':raise SystemExit('crash during cleanup')
            return original(operation,context)
        self.fixture.perform=crash
        with self.assertRaises(SystemExit):self.runner().run()
        self.fixture.perform=original
        report=self.runner(resume=True).run()
        self.assertEqual(report['runs'][0]['first_failure']['detail'],'sender launch reply lost')
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'HARNESS_FAIL')

    def test_changed_campaign_is_rejected_before_dispatch(self):
        self.runner().run()
        changed=self.suite();changed['identity']['build_id']='different'
        calls=len(self.hosts.calls)
        with self.assertRaises(ValueError):
            self.runner(changed,resume=True).run()
        self.assertEqual(len(self.hosts.calls),calls)

    def test_invalid_suite_is_rejected_before_any_dispatch(self):
        mutations=[lambda s:s['cases'][0]['repetitions'].pop(),
                   lambda s:s['cases'][0]['repetitions'][1].update(run_id='run-1'),
                   lambda s:s.update(case_timeout_seconds=float('inf')),
                   lambda s:s['cases'][0].update(name='shell'),
                   lambda s:s['cases'][0]['repetitions'][0].update(bundle_path='../relative')]
        for mutate in mutations:
            suite=self.suite();mutate(suite)
            with self.subTest(suite=suite),self.assertRaises(ValueError):
                self.runner(suite).run()
        self.assertEqual(self.hosts.calls,[])

    def test_no_fixture_is_explicitly_unsupported(self):
        runner=self.runner();runner.fixture=None
        report=runner.run()
        self.assertEqual(report['runs'][0]['TEST_RESULT'],'UNSUPPORTED')
        self.assertEqual(self.hosts.calls,[])


class TransportRunnerTest(unittest.TestCase):
    setUpClass=classmethod(RunnerTest.setUpClass.__func__)
    tearDownClass=classmethod(RunnerTest.tearDownClass.__func__)
    setUp=RunnerTest.setUp
    suite=RunnerTest.suite
    sleep=RunnerTest.sleep
    runner=RunnerTest.runner
    def test_actual_transport_subprocess_and_host_journal_cleanup(self):
        self.transport_case(False)

    def test_actual_transport_failure_cleans_exact_host_journal_owners(self):
        self.transport_case(True)

    def transport_case(self,fail):
        from scripts.multiserver.transport import SSHHostAdapter
        from scripts.multiserver.host_agent import PrivateRoot, instance_key
        from scripts.multiserver.ownership import HostJournal
        from multiserver_host_test import deployment
        configs={};endpoints={}
        for server in ('node-a','node-b'):
            root=self.output.parent/server;root.mkdir(mode=0o700)
            config=deployment(root)
            config['instances']=[]
            for n in range(1,4):
                inst=next(i for i in self.bundle['instances'] if i['server_id']==server)
                resource=deployment(root)['instances'][0]['resources']
                config['instances'].append(dict(run_id='run-'+str(n),bundle_id=self.bundle['bundle_id'],
                    server_id=server,gpu_id=0,observation_sha256=inst['observation_sha256'],
                    resources=resource,bundle_path=str(self.root/'bundle')))
            path=root/'host.json';path.write_text(json.dumps(config));path.chmod(0o600)
            configs[server]=config
            endpoints[server]=dict(ssh_host=server,python_path=sys.executable,
                host_entrypoint=str(Path(__file__).resolve()),known_hosts_file='/fixed/known_hosts',deployment_path=str(path))
        adapter=SSHHostAdapter(endpoints,ssh_path='/usr/bin/ssh',timeout_seconds=3)
        original=subprocess.Popen
        def local_peer(argv,**kwargs):
            import shlex
            remote=shlex.split(argv[-1]);path=remote[remote.index('--deployment')+1]
            return original([sys.executable,'-B',str(Path(__file__).resolve()),'--host-peer',path],**kwargs)
        original_perform=self.fixture.perform
        def fixture_perform(operation,context):
            if operation=='collect':
                for server,config in configs.items():
                    installed=next(i for i in config['instances'] if i['run_id']==context['run_id'])
                    root=PrivateRoot(config['root'])
                    try:snapshot=root.read('instance-'+instance_key(installed))
                    finally:root.close()
                    self.hosts.hosts[(server,0)].update(state=snapshot['state'],generation=snapshot['owner']['generation'])
                    journal=HostJournal(Path(config['root'])/'journal.sqlite')
                    try:self.assertTrue(journal.load(snapshot['owner'])['released'])
                    finally:journal.close()
            return original_perform(operation,context)
        self.fixture.perform=fixture_perform
        self.fixture.fail_arm=fail
        runner=self.runner();runner.adapter=adapter
        with patch('scripts.multiserver.transport.subprocess.Popen',side_effect=local_peer):
            result=runner.run()
        self.assertEqual([r['TEST_RESULT'] for r in result['runs']],['HARNESS_FAIL' if fail else 'PASS']*3)
        self.assertEqual([r['CLEANUP_RESULT'] for r in result['runs']],['PASS']*3)
        for config in configs.values():
            import sqlite3
            connection=sqlite3.connect(str(Path(config['root'])/'journal.sqlite'))
            try:
                records=[json.loads(r[0]) for r in connection.execute('SELECT document FROM owners')]
                self.assertEqual(len(records),3)
                self.assertTrue(all(r['released'] for r in records))
                self.assertEqual([sum(a['command']['operation']=='start' for a in r['actions'].values()) for r in records],[0 if fail else 1]*3)
            finally:connection.close()


def host_peer(path):
    from multiserver.host_agent import HostAgent, PrivateRoot
    from multiserver.ownership import HostJournal
    config=json.loads(Path(path).read_text());command=json.load(sys.stdin)
    class Runtime:
        def state(self,owner):
            root=PrivateRoot(config['root'])
            try:return root.read('runtime-'+owner['generation'])
            finally:root.close()
        def save(self,owner,value):
            root=PrivateRoot(config['root'])
            try:root.write('runtime-'+owner['generation'],value)
            finally:root.close()
            return value
        def prepare(self,owner,bundle_path):
            return self.save(owner,dict(sequence=1,state='READY',cleanup='PENDING',evidence=dict(
                target_data_complete=False,output_published=False,eod_complete=False),error=None,outcome='APPLIED',polls=0))
        def start(self,owner,utc_start,duration_seconds):
            value=self.state(owner);value.update(sequence=value['sequence']+1,state='RUNNING')
            self.save(owner,value);return {k:v for k,v in value.items() if k!='polls'}
        def observe(self,owner):
            value=self.state(owner);value['sequence']+=1;value['polls']+=1
            if value['state']=='RUNNING' and value['polls']>=2:
                value.update(state='COMPLETED',cleanup='CONFIRMED',evidence=dict(
                    target_data_complete=True,output_published=True,eod_complete=True))
            return self.report(owner,self.save(owner,value))
        def stop(self,owner,mode,timeout_seconds):
            value=self.state(owner);value.update(sequence=value['sequence']+1,state='INTERRUPTED',cleanup='CONFIRMED')
            return self.report(owner,self.save(owner,value))
        def report(self,owner,value):
            result={k:v for k,v in value.items() if k!='polls'}
            if value['cleanup']=='CONFIRMED':
                installed=next(i for i in config['instances'] if i['run_id']==owner['run_id'])
                result['cleanup_evidence']=dict(owner=owner,resources=installed['resources'],
                    processes_gone=True,descendants_gone=True,resources_gone=True)
            return result
    runtime=Runtime();prepare=runtime.prepare
    def without_polls(*args):return {k:v for k,v in prepare(*args).items() if k!='polls'}
    runtime.prepare=without_polls
    journal=HostJournal(Path(config['root'])/'journal.sqlite')
    try:
        result=HostAgent(journal,object(),runtime,config).handle(command)
        print(json.dumps(result))
    finally:journal.close()


class SenderPeerTest(unittest.TestCase):
    def patch_fault_compiler(self,fixture):
        # The fixed peer imports the other package namespace. Patch that exact
        # class only for hardware-bound fault unit cases, not production timing.
        compiler_patch=patch.object(self.peer.PipelineRuntime,'_run_compiler',
            side_effect=lambda session,argv,timeout:fixture.compile(argv))
        compiler_patch.start();fixture.addCleanup(compiler_patch.stop)

    def setUp(self):
        import importlib.util
        path=Path(__file__).resolve().parent/'fixtures/multiserver_acceptance_peer.py'
        self.assertTrue(path.exists(),'fixed sender peer missing')
        spec=importlib.util.spec_from_file_location('acceptance_peer',str(path))
        self.peer=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.peer)
        self.tmp=tempfile.TemporaryDirectory(prefix='ms-i1-peer-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.binary=self.root/'fpga_sender_sim'
        self.binary.write_text('#!/usr/bin/env python3\nprint(123)\n')
        self.binary.chmod(0o700)
        config=dict(schema_version=3,source=dict(ip='127.0.0.1',port=41000),
            destination=dict(ip='127.0.0.1',port=4010,path_mtu=9000),station=dict(station_ids=[1000]),
            packet=dict(first_channel_id=100,nchan=4,npol=1,nsamp_per_packet=512,
                        component_bits=8,sample_interval_ps='1000000'),
            time=dict(reference_epoch=60,start_seconds=0,mode='PACED',start_utc='2030-01-01-00:00:00',group_count=2048),
            transmit=dict(target_gbps=0.1,batch_packets=16,payload_mode='REPEAT_TEMPLATE'),
            faults=dict(drop_groups=[],duplicate_groups=[],invalid_header_groups=[]))
        self.config_path=self.root/'sender.json';self.config_path.write_text(json.dumps(config))
        self.context=dict(identity=dict(source_revision='2b746a4',source_manifest_sha256='c'*64,build_id='fixed'),
            suite_id='suite',run_id='run',case='finite_eod',bundle_id='d'*64,
            observation=dict(utc_start='2030-01-01-00:00:00',duration_seconds='1.048576',
                             station_ids=[1000],npol=1,sample_interval_ps=1000000),
            samples_per_packet=512,destinations=[dict(server_id='host',gpu_id=0,
                match=dict(destination_ip='127.0.0.1',destination_port=4010))],assignments=[dict(server_id='host',gpu_id=0,first_channel_id=100,nchan=4)])
        sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        self.config=dict(schema_version=1,role='sender',server_id='qtp1',root=str(self.root/'state'),
            identity=self.context['identity'],lifetime_seconds=2,binary=dict(path=str(self.binary),sha256=sha(self.binary),leaf=True),
            jobs=[dict(run_id='run',bundle_id='d'*64,server_id='host',gpu_id=0,observation_sha256='e'*64,
                       config_path=str(self.config_path),config_sha256=sha(self.config_path))])

    def test_fixed_sender_validates_allocation_and_refuses_changed_bytes_before_launch(self):
        checked=self.peer.check_sender(self.config,self.context)
        self.assertEqual(checked[0]['argv'],[str(self.binary),str(self.config_path)])
        self.config_path.write_text('{}')
        with self.assertRaises(ValueError):
            self.peer.check_sender(self.config,self.context)
        self.assertFalse((self.root/'state').exists())

    def test_wrong_destination_channel_or_start_refused(self):
        original=json.loads(self.config_path.read_text())
        for field,value in [('nchan',5),('nsamp_per_packet',1024)]:
            cfg=copy.deepcopy(original);cfg['packet'][field]=value
            self.config_path.write_text(json.dumps(cfg))
            self.config['jobs'][0]['config_sha256']=hashlib.sha256(self.config_path.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):self.peer.check_sender(self.config,self.context)

    def test_runtime_failure_is_not_falsely_equated_with_sender_stop(self):
        context=dict(self.context,case='runtime_failure')
        result=self.peer.check(self.config,context)
        self.assertTrue(result['supported'])  # sender is valid, receiver must own fault

    def test_pipeline_fault_uses_only_retained_gpu_identity_and_target_owner(self):
        from multiserver_pipeline_runtime_test import RuntimeTest
        fixture=RuntimeTest(methodName='runTest')
        fixture.setUp()
        self.patch_fault_compiler(fixture)
        try:
            config=copy.deepcopy(fixture.config)
            config['runtime']['kind']='pipeline-fixture'
            config['runtime']['fault']=[dict(run_id='run',server_id='node-a',gpu_id=0,
                                           after_running_seconds=0.01,role='gpu')]
            runtime=self.peer.FaultPipelineRuntime(config,fixture.supervisor,journal=fixture.journal,
                platform=fixture.platform,utc_now=lambda:fixture.clock)
            fixture.runtime.close();fixture.runtime=runtime
            fixture.addCleanup(runtime.close)
            self.assertEqual(fixture.prepare()['state'],'READY')
            fixture.start()
            fixture.clock=datetime.datetime(2026,9,12,tzinfo=datetime.timezone.utc)+datetime.timedelta(seconds=.02)
            observed=runtime.observe(fixture.owner)
            self.assertEqual(observed['state'],'FAILED')
            import signal
            self.assertIn(('one','gpu',signal.SIGKILL),fixture.supervisor.signals)
            proof=json.loads((fixture.root/'runs/node-a-gpu0/fault.json').read_text())
            self.assertEqual(proof['owner'],fixture.owner)
            count=len(fixture.supervisor.signals)
            runtime.observe(fixture.owner)
            self.assertEqual(len(fixture.supervisor.signals),count)
        finally:
            fixture.doCleanups()

    def test_fixed_host_wrapper_enters_real_cli_and_refuses_client_overrides(self):
        from multiserver_host_test import deployment, command
        host=deployment(self.root/'host')
        host['runtime']=dict(kind='pipeline-fixture',binaries={},environment={},ipc={},
            ready_timeout_seconds=1,drain_timeout_seconds=1,poll_interval_seconds=.01,fault={})
        path=self.root/'host.json';path.write_text(json.dumps(host));path.chmod(0o600)
        peer_path=Path(self.peer.__file__)
        for payload in [command('status'),dict(command('status'),development_fixture=True),
                        dict(command('status'),deployment='/client/path')]:
            result=subprocess.run([sys.executable,'-B',str(peer_path),'--deployment',str(path)],
                input=json.dumps(payload),text=True,capture_output=True,timeout=4)
            if len(payload)==9:
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout)['state'],'NEW')
            else:self.assertNotEqual(result.returncode,0)

    def test_negative_bundle_requires_one_declared_private_independent_deviation(self):
        from multiserver_pipeline_runtime_test import RuntimeTest
        import shutil
        fixture=RuntimeTest(methodName='runTest');fixture.setUp()
        try:
            self.assertTrue(hasattr(self.peer,'negative_fixture'),'negative bundle identity validator missing')
            baseline=fixture.bundle_path;negative=self.root/'negative'
            shutil.copytree(baseline,negative)
            selected=fixture.bundle['instances'][1]
            relative=selected['path']+'/observation.json';file=negative/relative
            original=self.peer.sha(file);file.write_bytes(file.read_bytes()+b'\n')
            installed=dict(run_id='run',bundle_id=fixture.bundle['bundle_id'],server_id='node-b',gpu_id=0,
                observation_sha256=selected['observation_sha256'],bundle_path=str(negative))
            declaration=dict(schema_version=1,run_id='run',bundle_id=fixture.bundle['bundle_id'],server_id='node-b',gpu_id=0,
                expected_error='BUNDLE_MANIFEST',baseline_manifest_sha256=self.peer.sha(baseline/'MANIFEST.sha256'),
                relative_path=relative,original_sha256=original,negative_sha256=self.peer.sha(file))
            sidecar=Path(str(negative)+'.acceptance-negative.json')
            sidecar.write_text(json.dumps(declaration));sidecar.chmod(0o600)
            context=dict(self.context,bundle_id=fixture.bundle['bundle_id'],case='prepare_rollback',
                bundle_manifest_sha256=declaration['baseline_manifest_sha256'],
                prepare_failure=dict(server_id='node-b',gpu_id=0,expected_error='BUNDLE_MANIFEST'))
            proof=self.peer.negative_fixture(installed,context,[str(baseline)])
            self.assertEqual(proof['negative_sha256'],declaration['negative_sha256'])
            self.assertEqual(self.peer.sha(baseline/relative),original)
            sidecar.write_text(json.dumps(dict(declaration,gpu_id=False)))
            with self.assertRaises(ValueError):self.peer.negative_fixture(installed,context,[str(baseline)])
            sidecar.write_text(json.dumps(declaration))
            other=negative/'global-observation.json';before=other.read_bytes();other.write_bytes(before+b'\n')
            with self.assertRaises(ValueError):self.peer.negative_fixture(installed,context,[str(baseline)])
            other.write_bytes(before)
            file.rename(negative/'moved')
            file.symlink_to(negative/'moved')
            with self.assertRaises(ValueError):self.peer.negative_fixture(installed,context,[str(baseline)])
            file.unlink();(negative/'moved').rename(file)
            os.link(str(file),str(self.root/'shared-modified-file'))
            with self.assertRaises(ValueError):self.peer.negative_fixture(installed,context,[str(baseline)])
            sidecar.unlink()
            with self.assertRaises(ValueError):self.peer.negative_fixture(installed,context,[str(baseline)])
        finally:fixture.doCleanups()

    def test_three_real_controller_host_runtime_prepare_rollbacks_have_allocation_and_reap_proof(self):
        from multiserver_pipeline_runtime_test import RuntimeTest, FakeSupervisor, FakePlatform, local_retained_supervisor
        from scripts.multiserver.host_agent import HostAgent
        from scripts.multiserver.pipeline_runtime import PipelineRuntime
        import shutil
        fixture=RuntimeTest(methodName='runTest');fixture.setUp()
        try:
            peer_owner,_=fixture.add_peer()
            templates=[fixture.journal.load(owner)['resources'] for owner in (fixture.owner,peer_owner)]
            for owner,resources in zip((fixture.owner,peer_owner),templates):
                fixture.journal.release(owner,dict(owner=owner,resources=resources,processes_gone=True,
                    descendants_gone=True,resources_gone=True))
            config=copy.deepcopy(fixture.config);config['instances']=[]
            suite=RunnerTest.suite(self,'prepare_rollback')
            # Reuse the bounded suite shape with this test's real, per-run bundles.
            suite['identity']=self.context['identity']
            profiles_path=fixture.root/'peer-inputs/deployments.json'
            for number,item in enumerate(suite['cases'][0]['repetitions'],1):
                profiles=json.loads(profiles_path.read_text())
                profiles['profiles']['example-node-a-gpu0']['cpu']['unpack']=[1,6]
                for index,profile in enumerate(profiles['profiles'].values()):
                    profile['paths']['run_dir']='runs/rollback-{}-gpu{}'.format(number,index)
                profiles_path.write_text(json.dumps(profiles))
                good=fixture.root/('rollback-bundle-'+str(number))
                bundle=build_bundle(fixture.root/'peer-inputs/global.json',profiles_path,good,
                    inventory_path=fixture.root/'peer-inputs/inventory.json')
                bad=fixture.root/('negative-bundle-'+str(number));shutil.copytree(good,bad)
                relative=bundle['instances'][1]['path']+'/observation.json'
                changed=bad/relative;original=self.peer.sha(changed);changed.write_bytes(changed.read_bytes()+b'\n')
                sidecar=Path(str(bad)+'.acceptance-negative.json')
                sidecar.write_text(json.dumps(dict(schema_version=1,run_id=item['run_id'],bundle_id=bundle['bundle_id'],
                    server_id='node-a',gpu_id=1,expected_error='BUNDLE_MANIFEST',
                    baseline_manifest_sha256=self.peer.sha(good/'MANIFEST.sha256'),relative_path=relative,
                    original_sha256=original,negative_sha256=self.peer.sha(changed))))
                sidecar.chmod(0o600)
                item.update(bundle_path=str(good),global_path=str(good/'global-observation.json'),
                    prepare_failure=dict(server_id='node-a',gpu_id=1,expected_error='BUNDLE_MANIFEST'))
                for index,instance in enumerate(bundle['instances']):
                    resources=copy.deepcopy(templates[index])
                    resources['run_dir']=str(fixture.root/instance['deployment']['paths']['run_dir'])
                    config['instances'].append(dict(run_id=item['run_id'],bundle_id=bundle['bundle_id'],
                        server_id='node-a',gpu_id=index,observation_sha256=instance['observation_sha256'],
                        bundle_path=str(good if index==0 else bad),resources=resources))
            base=fixture.root
            fixture.resolver_patch.stop()
            stub=base/'finite-compiler'
            tests_path=str(Path(__file__).resolve().parent)
            repository=str(Path(__file__).resolve().parents[1])
            stub.write_text('#!'+str(Path(sys.executable).resolve())+'\n'+
                'import sys\n'+
                'sys.path.insert(0, '+repr(repository)+')\n'+
                'sys.path.insert(0, '+repr(tests_path)+')\n'+
                'from multiserver_pipeline_runtime_test import RuntimeTest\n'+
                'result=RuntimeTest.compile(None,sys.argv)\n'+
                'sys.stdout.write(result.stdout)\n'+
                'sys.exit(result.returncode)\n')
            stub.chmod(0o700)
            config['runtime']['binaries']['compiler']=dict(path=str(stub),sha256=self.peer.sha(stub),leaf=True)
            # A local finite child needs a realistic test deadline; product
            # defaults and failure behavior remain untouched.
            config['runtime']['ready_timeout_seconds']=2
            retained_context=local_retained_supervisor(base/'processes')
            retained=retained_context.__enter__()
            fixture.addCleanup(retained_context.__exit__,None,None,None)
            class Supervisor(FakeSupervisor):
                def __init__(inner):
                    super(Supervisor,inner).__init__()
                    inner.retained_compiler=retained
                def persist(inner):
                    for (generation,role),report in inner.reports.items():
                        directory=base/'processes'/(generation+'-'+role);directory.mkdir(parents=True,exist_ok=True)
                        identity=report['identity']
                        process={k:v for k,v in identity.items() if k not in ('owner','role')}
                        (directory/'launch.json').write_text(json.dumps(dict(version=1,owner=identity['owner'],role=role,
                            argv=['/fixed/test-binary'],cwd=str(base),env={},state=report['state'],
                            process=process,returncode=report['returncode'],error=None)))
                def launch(inner,*args):
                    if args[1]=='compiler':return retained.launch(*args)
                    result=super(Supervisor,inner).launch(*args);inner.persist();return result
                def inspect(inner,*args):
                    if args[0]['role']=='compiler':return retained.inspect(*args)
                    result=super(Supervisor,inner).inspect(*args);inner.persist();return result
                def signal(inner,*args):
                    if args[0]['role']=='compiler':return retained.signal(*args)
                    result=super(Supervisor,inner).signal(*args);inner.persist();return result
                def wait(inner,*args):
                    if args[0]['role']=='compiler':return retained.wait(*args)
                    return super(Supervisor,inner).wait(*args)
            class Platform(FakePlatform):
                def capture_ring(inner,ring,report,footprint):
                    token=super(Platform,inner).capture_ring(ring,report,footprint)
                    token['objects']=[dict(kind=o['kind'],metadata=dict(key=o['key'],
                        object_id=index,cpid=report['identity']['pid'])) for index,o in enumerate(footprint)]
                    return token
            supervisor=Supervisor();platform=Platform()
            config.update(root=str(base),stop_timeout_seconds=.03,request_timeout_seconds=.1)
            runtime=PipelineRuntime(config,supervisor,journal=fixture.journal,platform=platform,utc_now=lambda:fixture.clock)
            fixture.runtime.close();fixture.addCleanup(runtime.close)
            agent=HostAgent(fixture.journal,supervisor,runtime,config)
            deployment_path=base/'host-deployment.json';deployment_path.write_text(json.dumps(config));deployment_path.chmod(0o600)
            audit=dict(schema_version=1,role='receiver',server_id='receiver',root=str(base/'audits'),
                identity=suite['identity'],host_deployment_path=str(deployment_path),host_deployment_sha256=self.peer.sha(deployment_path))
            peer=self.peer
            class Hosts:
                def send(inner,key,command):return agent.handle(command)
            class RollbackFixture:
                def perform(inner,operation,context):
                    if operation=='check':
                        checked=peer.check(audit,context)
                        return dict(identity=context['identity'],supported=True,
                            negative_fixtures=[checked['negative_fixture']])
                    if operation=='retire':
                        runtime.close();return dict(identity=context['identity'],host_supervisors_gone=True)
                    with patch.object(peer.LinuxPlatform,'_objects',return_value={}):
                        result=peer.receiver_collect(audit,context,cleanup=operation=='cleanup')
                    return dict(result,identity=context['identity'])
            report=AcceptanceRunner(suite,base/'campaign',host_adapter=Hosts(),fixture=RollbackFixture(),
                utc_now=lambda:fixture.clock).run()
            self.assertEqual([r['TEST_RESULT'] for r in report['runs']],['PASS']*3,
                             [r['first_failure'] for r in report['runs']])
            self.assertEqual([r['CLEANUP_RESULT'] for r in report['runs']],['PASS']*3)
            self.assertEqual(len(platform.owned),9)
            self.assertEqual(len(platform.removed),9)
            compiler_pids={next(p for p in run['collection']['launches'][0]['processes']
                                if p['role']=='compiler')['process']['pid'] for run in report['runs']}
            children=list(supervisor.retained_compiler._children.values())
            self.assertEqual({child.pid for child in children},compiler_pids)
            self.assertEqual([child.returncode for child in children],[0,0,0])
            validator=AcceptanceRunner(suite,base/'validation-only',host_adapter=Hosts(),fixture=RollbackFixture())
            first=report['runs'][0]
            trace=[json.loads(line) for line in (base/'campaign/run-1/events.jsonl').read_text().splitlines()]
            validator._host_trace=[{k:e[k] for k in ('instance','command','report')}
                                   for e in trace if e['event']=='host_report']
            ready=validator._verify_rollback_history(suite['cases'][0]['repetitions'][0]['prepare_failure'])
            compiled=copy.deepcopy(first['collection'])
            compiled_owner=next(r for r in compiled['launches'] if r['gpu_id']==0)
            self.assertEqual(len(compiled_owner['processes']),8)
            validator._verify_rollback_resources(ready,compiled)
            for failure in ('missing','running','nonzero','duplicate','foreign-role','foreign-owner','reused-identity'):
                broken=copy.deepcopy(compiled)
                row=next(r for r in broken['launches'] if r['gpu_id']==0)
                candidate=next(p for p in row['processes'] if p['role']=='compiler')
                if failure=='missing':row['processes'].remove(candidate)
                elif failure=='running':candidate.update(state='RUNNING',returncode=None)
                elif failure=='nonzero':candidate['returncode']=7
                elif failure=='duplicate':row['processes'].append(copy.deepcopy(candidate))
                elif failure=='foreign-role':candidate['role']='unknown-helper'
                elif failure=='foreign-owner':candidate['owner']=dict(candidate['owner'],generation='foreign')
                else:candidate['process']=copy.deepcopy(next(p for p in row['processes'] if p['role']!='compiler')['process'])
                with self.subTest(compiler=failure),self.assertRaises(ValueError):
                    validator._verify_rollback_resources(ready,broken)
            for field in ('processes','ring_tokens','ipc_absent','claim'):
                broken=copy.deepcopy(first['collection'])
                row=next(r for r in broken['launches'] if r['gpu_id']==0)
                row.pop(field)
                with self.subTest(missing=field),self.assertRaises(ValueError):
                    validator._verify_rollback_resources(ready,broken)
            validator._host_trace=[e for e in validator._host_trace if e['command']['operation']!='stop']
            with self.assertRaises(ValueError):
                validator._verify_rollback_history(suite['cases'][0]['repetitions'][0]['prepare_failure'])
            audit_path=next(Path(a['path']) for a in first['collection']['artifacts']
                            if Path(a['path']).name.startswith('collect-'))
            context=json.loads(audit_path.read_text())['context']
            changed=Path(first['negative_fixture']['negative_root'])/first['negative_fixture']['relative_path']
            changed.write_bytes(changed.read_bytes()+b'additional undeclared change\n')
            with patch.object(peer.LinuxPlatform,'_objects',return_value={}),self.assertRaises(ValueError):
                peer.receiver_collect(audit,context)
        finally:fixture.doCleanups()

    def test_paced_sender_budget_must_fit_original_data_interval(self):
        cfg=json.loads(self.config_path.read_text());cfg['transmit']['target_gbps']=0.001
        self.config_path.write_text(json.dumps(cfg))
        self.config['jobs'][0]['config_sha256']=hashlib.sha256(self.config_path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):self.peer.check_sender(self.config,self.context)

    def receiver_fixture(self):
        from multiserver_host_test import deployment, command
        from multiserver.host_agent import HostAgent, PrivateRoot, instance_key
        from multiserver.ownership import HostJournal
        root=self.root/'receiver';root.mkdir(mode=0o700)
        host=deployment(root);installed=host['instances'][0]
        installed.update(bundle_id='d'*64)
        cmd=command();cmd.update({k:installed[k] for k in ('run_id','bundle_id','server_id','gpu_id','observation_sha256')})
        snapshot=HostAgent._initial(cmd);owner=snapshot['owner']
        snapshot.update(state='COMPLETED',cleanup='CONFIRMED')
        private=PrivateRoot(str(root))
        try:private.write('instance-'+instance_key(installed),snapshot)
        finally:private.close()
        journal=HostJournal(root/'journal.sqlite')
        try:
            journal.claim(owner,installed['resources'])
            journal.release(owner,dict(owner=owner,resources=installed['resources'],processes_gone=True,
                                       descendants_gone=True,resources_gone=True))
        finally:journal.close()
        directory=Path(installed['resources']['run_dir']);(directory/'compiled').mkdir(parents=True)
        ids=dict(config_id='a'*64,geometry_id='b'*64)
        records={'receiver':dict(pid=100,committed_bytes=128,eod=True),
                 'unpack':dict(pid=101,station_accepted_packets=[2],emitted_bytes=256,eod=True),
                 'gpu':dict(pid=102,input_bytes=256,output_bytes=128,published_blocks=2,output_eod=True)}
        for role,value in records.items():
            (directory/(role+'.result')).write_text(json.dumps(dict(ids,**value)))
            proc=root/'processes'/role;proc.mkdir(parents=True)
            (proc/'launch.json').write_text(json.dumps(dict(owner=owner,role=role,state='EXITED',returncode=0,
                                                           process=dict(pid=value['pid'],start_ticks=10,boot_id='test-boot',
                                                               uid=os.getuid(),pgid=value['pid'],sid=value['pid']))))
        (directory/'compiled/validation_report.json').write_text(json.dumps(dict(ids,derived=dict(raw_record_bytes=64,expected_groups=2))))
        (directory/'compiled/ring_plan.json').write_text(json.dumps(dict(rings=dict(
            compute=dict(block_bytes=128),output=dict(block_bytes=64)))))
        path=self.root/'host-audit.json';path.write_text(json.dumps(host));path.chmod(0o600)
        config=dict(schema_version=1,role='receiver',server_id='host',root=str(self.root/'audits'),identity=self.context['identity'],
                    host_deployment_path=str(path),host_deployment_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return config,dict(self.context,samples_per_block=512),directory

    def test_receiver_audit_reads_exact_product_identity_and_checks_actual_residue(self):
        config,context,directory=self.receiver_fixture()
        journal=directory.parent/'journal.sqlite'
        before=journal.read_bytes()
        result=self.peer.receiver_collect(config,context)
        self.assertEqual(result['instances'][0]['receiver_packets'],2)
        self.assertEqual(journal.read_bytes(),before)
        with patch.object(self.peer.LinuxPlatform,'_objects',return_value={}):
            self.assertTrue(self.peer.receiver_collect(config,context,True)['resources_gone'])
        value=json.loads((directory/'gpu.result').read_text());value['pid']=999
        (directory/'gpu.result').write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.peer.receiver_collect(config,context)

    def test_receiver_collector_reads_and_validates_fixed_fault_against_gpu_ledger(self):
        config,context,directory=self.receiver_fixture()
        path=Path(config['host_deployment_path']);host=json.loads(path.read_text())
        host['runtime']=dict(kind='pipeline-fixture',fault=[dict(run_id='run',server_id='host',gpu_id=0,
            after_running_seconds=.01,role='gpu')])
        path.write_text(json.dumps(host));config['host_deployment_sha256']=self.peer.sha(path)
        private=self.peer.PrivateRoot(host['root'])
        installed=host['instances'][0];name='instance-'+self.peer.instance_key(installed)
        try:
            snapshot=private.read(name);snapshot['state']='FAILED';private.write(name,snapshot)
        finally:private.close()
        owner=snapshot['owner'];ledger=Path(host['root'])/'processes/gpu/launch.json'
        process=json.loads(ledger.read_text());process['returncode']=-9;ledger.write_text(json.dumps(process))
        proof=dict(owner=owner,identity=dict(process['process'],owner=owner,role='gpu'),role='gpu',signal='SIGKILL')
        fault=directory/'fault.json';fault.write_text(json.dumps(proof))
        context.update(case='runtime_failure',target=['uninstalled-client-target',99])
        result=self.peer.receiver_collect(config,context)
        self.assertIn('fault_records',result)
        self.assertEqual(result['fault_records'][0]['proof'],proof)
        self.assertEqual(result['fault_records'][0]['artifact']['sha256'],self.peer.sha(fault))
        self.assertEqual(result['fault_targets'][0]['binding']['server_id'],'host')
        self.assertEqual(result['fault_targets'][0]['binding']['gpu_id'],0)
        for field,value in [('owner',dict(owner,generation='foreign')),('role','receiver'),('signal','SIGTERM'),
                            ('identity',dict(proof['identity'],pid=999))]:
            fault.write_text(json.dumps(dict(proof,**{field:value})))
            with self.subTest(field=field),self.assertRaises(ValueError):self.peer.receiver_collect(config,context)
        with patch.object(self.peer.LinuxPlatform,'_objects',return_value={}):
            self.assertTrue(self.peer.receiver_collect(config,context,True)['resources_gone'])
        fault.write_text(json.dumps(proof));process['returncode']=7;ledger.write_text(json.dumps(process))
        with self.assertRaises(ValueError):self.peer.receiver_collect(config,context)
        fault.unlink()
        with self.assertRaises(ValueError):self.peer.receiver_collect(config,context)

    def test_fault_schema_refuses_invalid_targets_and_delay_before_effects(self):
        from multiserver_pipeline_runtime_test import RuntimeTest
        fixture=RuntimeTest(methodName='runTest');fixture.setUp()
        try:
            config=copy.deepcopy(fixture.config);config['runtime']['kind']='pipeline-fixture'
            fault=dict(run_id='run',server_id='node-a',gpu_id=0,after_running_seconds=.01,role='gpu')
            values=[[],[fault]*4,[fault,fault],fault,[dict(fault,run_id='absent')]]
            values += [[dict(fault,after_running_seconds=v)] for v in (True,float('nan'),float('inf'),-.1,2.0)]
            for value in values:
                with self.subTest(fault=value),self.assertRaises(ValueError):
                    self.peer.FaultPipelineRuntime(dict(config,runtime=dict(config['runtime'],fault=value)),
                        fixture.supervisor,journal=fixture.journal,platform=fixture.platform)
            self.assertEqual(fixture.supervisor.launches,[])
        finally:fixture.doCleanups()

    def test_fixed_fault_does_not_stop_samehost_healthy_gpu(self):
        from multiserver_pipeline_runtime_test import RuntimeTest
        fixture=RuntimeTest(methodName='runTest');fixture.setUp()
        self.patch_fault_compiler(fixture)
        try:
            owner,path=fixture.add_peer()
            config=copy.deepcopy(fixture.runtime.config);config['runtime']['kind']='pipeline-fixture'
            config['runtime']['fault']=[dict(run_id='run',server_id='node-a',gpu_id=0,after_running_seconds=.01,role='gpu')]
            runtime=self.peer.FaultPipelineRuntime(config,fixture.supervisor,journal=fixture.journal,
                platform=fixture.platform,utc_now=lambda:fixture.clock)
            fixture.runtime.close();fixture.runtime=runtime;fixture.addCleanup(runtime.close)
            self.assertEqual(fixture.prepare()['state'],'READY')
            self.assertEqual(runtime.prepare(owner,str(path))['state'],'READY')
            fixture.start();runtime.start(owner,'2026-09-12-00:00:00','1.048576')
            fixture.clock=datetime.datetime(2026,9,12,tzinfo=datetime.timezone.utc)+datetime.timedelta(seconds=.02)
            self.assertEqual(runtime.observe(fixture.owner)['state'],'FAILED')
            self.assertEqual(runtime.observe(owner)['state'],'RUNNING')
            self.assertFalse(any(generation=='two' for generation,_,_ in fixture.supervisor.signals))
        finally:fixture.doCleanups()

    def test_three_failure_lifecycles_reuse_one_journal_and_runtime_after_release(self):
        from multiserver_pipeline_runtime_test import RuntimeTest
        from scripts.multiserver.host_agent import HostAgent
        fixture=RuntimeTest(methodName='runTest');fixture.setUp()
        self.patch_fault_compiler(fixture)
        try:
            fixture.journal.release(fixture.owner,dict(owner=fixture.owner,resources=fixture.resources,
                processes_gone=True,descendants_gone=True,resources_gone=True))
            config=copy.deepcopy(fixture.config);config['runtime']['kind']='pipeline-fixture'
            config['runtime']['fault']=[];config['instances']=[]
            for n in range(1,4):
                deployments_path=fixture.root/'inputs/deployments.json'
                profiles=json.loads(deployments_path.read_text())
                profiles['profiles']['example-node-a-gpu0']['paths']['run_dir']='runs/fault-'+str(n)
                deployments_path.write_text(json.dumps(profiles))
                path=fixture.root/('fault-bundle-'+str(n))
                bundle=build_bundle(fixture.root/'inputs/global.json',deployments_path,path,
                    inventory_path=fixture.root/'inputs/inventory.json')
                instance=bundle['instances'][0]
                resources=copy.deepcopy(fixture.resources);resources['run_dir']=str(fixture.root/('runs/fault-'+str(n)))
                installed=dict(run_id='run-'+str(n),bundle_id=bundle['bundle_id'],server_id='node-a',gpu_id=0,
                    observation_sha256=instance['observation_sha256'],bundle_path=str(path),resources=resources)
                config['instances'].append(installed)
                config['runtime']['fault'].append(dict(run_id=installed['run_id'],server_id='node-a',gpu_id=0,
                    after_running_seconds=.01,role='gpu'))
            config.update(root=str(fixture.root),stop_timeout_seconds=.03,request_timeout_seconds=.1)
            runtime=self.peer.FaultPipelineRuntime(config,fixture.supervisor,journal=fixture.journal,
                platform=fixture.platform,utc_now=lambda:fixture.clock)
            fixture.runtime.close();fixture.addCleanup(runtime.close)
            agent=HostAgent(fixture.journal,fixture.supervisor,runtime,config)
            for installed in config['instances']:
                fixture.clock=datetime.datetime(2026,9,11,tzinfo=datetime.timezone.utc)
                binding={k:installed[k] for k in ('run_id','bundle_id','server_id','gpu_id','observation_sha256')}
                prepared=agent.handle(dict(binding,operation='prepare',action_id=installed['run_id']+'-p',expected_generation=None))
                self.assertEqual(prepared['state'],'READY',prepared)
                generation=prepared['generation']
                agent.handle(dict(binding,operation='start',action_id=installed['run_id']+'-s',expected_generation=generation,
                    utc_start='2026-09-12-00:00:00',duration_seconds='1.048576'))
                fixture.clock=datetime.datetime(2026,9,12,tzinfo=datetime.timezone.utc)+datetime.timedelta(seconds=.02)
                observed=agent.handle(dict(binding,operation='status',action_id='query',expected_generation=generation,pending_action_ids=[]))
                self.assertEqual((observed['state'],observed['cleanup']),('FAILED','CONFIRMED'))
                self.assertTrue(fixture.journal.load(dict(binding,generation=generation))['released'])
        finally:fixture.doCleanups()

    def test_three_trusted_fault_targets_share_one_runtime_without_config_replacement(self):
        from multiserver_pipeline_runtime_test import RuntimeTest
        fixture=RuntimeTest(methodName='runTest');fixture.setUp()
        try:
            config=copy.deepcopy(fixture.config)
            config['runtime']['kind']='pipeline-fixture'
            config['runtime']['fault']=[]
            original=copy.deepcopy(config['instances'][0])
            config['instances']=[]
            for n in range(1,4):
                installed=dict(original,run_id='run-'+str(n));config['instances'].append(installed)
                config['runtime']['fault'].append(dict(run_id=installed['run_id'],server_id='node-a',gpu_id=0,
                    after_running_seconds=.01,role='gpu'))
            runtime=self.peer.FaultPipelineRuntime(config,fixture.supervisor,journal=fixture.journal,
                platform=fixture.platform,utc_now=lambda:fixture.clock)
            try:
                for n in range(1,4):self.assertTrue(runtime._target(dict(fixture.owner,run_id='run-'+str(n))))
                self.assertFalse(runtime._target(dict(fixture.owner,run_id='healthy-run')))
            finally:runtime.close()
        finally:fixture.doCleanups()

    def test_real_host_factory_constructs_only_fixed_fault_runtime(self):
        from multiserver_pipeline_runtime_test import RuntimeTest
        from multiserver_host import build_runtime
        fixture=RuntimeTest(methodName='runTest');fixture.setUp()
        try:
            config=copy.deepcopy(fixture.config)
            config['runtime']['binaries']['gpu_probe']=copy.deepcopy(config['runtime']['binaries']['compiler'])
            config['runtime']['kind']='pipeline-fixture'
            config['runtime']['fault']=[dict(run_id='run',server_id='node-a',gpu_id=0,
                                           after_running_seconds=.01,role='gpu')]
            with self.assertRaises(ValueError):build_runtime(config,fixture.supervisor,journal=fixture.journal)
            runtime=build_runtime(config,fixture.supervisor,True,journal=fixture.journal)
            try:self.assertEqual(type(runtime).__name__,'FaultPipelineRuntime')
            finally:runtime.close()
        finally:fixture.doCleanups()

    def test_real_peer_fixture_inject_dispatches_both_roles_without_sender_effects(self):
        from scripts.multiserver_acceptance import PeerFixture
        from scripts.multiserver.transport import SSHHostAdapter
        receiver,context,_=self.receiver_fixture()
        host_path=Path(receiver['host_deployment_path'])
        host=json.loads(host_path.read_text())
        host['runtime']=dict(kind='pipeline-fixture',fault=[dict(
            run_id='run',server_id='host',gpu_id=0,after_running_seconds=.01,role='gpu')])
        host_path.write_text(json.dumps(host))
        receiver['host_deployment_sha256']=hashlib.sha256(host_path.read_bytes()).hexdigest()
        paths={}
        for name,config in [('sender',self.config),('receiver',receiver)]:
            path=self.root/(name+'-peer.json')
            path.write_text(json.dumps(config));path.chmod(0o600)
            paths[name]=path
        endpoints={name:dict(ssh_host=name,python_path=sys.executable,
            host_entrypoint=str(self.peer.__file__),known_hosts_file='/fixed/known_hosts',
            deployment_path=str(path)) for name,path in paths.items()}
        fixture=PeerFixture(dict(fixture_endpoints=endpoints,ssh_path='/usr/bin/ssh',transport_timeout_seconds=3))
        # The receiver peer ID is deployment-selected; use the actual configured IDs.
        sender=json.loads(paths['sender'].read_text());sender['server_id']='sender'
        paths['sender'].write_text(json.dumps(sender))
        receiver['server_id']='receiver';paths['receiver'].write_text(json.dumps(receiver))
        context.update(case='runtime_failure',target=['uninstalled-client-target',99])
        original=subprocess.Popen
        def local_peer(argv,**kwargs):
            import shlex
            remote=shlex.split(argv[-1])
            return original(remote,**kwargs)
        before=host_path.read_bytes()
        with patch('scripts.multiserver.transport.subprocess.Popen',side_effect=local_peer):
            checked=fixture.perform('check',context)
            result=fixture.perform('inject',context)
        self.assertEqual(len(checked['fault_targets']),1)
        self.assertEqual(checked['fault_targets'][0]['binding']['server_id'],'host')
        self.assertEqual(checked['fault_targets'][0]['binding']['gpu_id'],0)
        self.assertTrue(result['applied'])
        self.assertFalse(Path(self.config['root']).exists())
        self.assertEqual(host_path.read_bytes(),before)

    def test_retained_supervisor_exact_stop_and_reap(self):
        from multiserver_pipeline_runtime_test import FakeSupervisor
        supervisor=FakeSupervisor()
        session=self.peer.SenderSession(self.config,self.context,supervisor)
        session.arm()
        self.assertEqual(supervisor.launches[0][2],[str(self.binary),str(self.config_path)])
        result=session.cleanup()
        self.assertTrue(result['sender_processes_gone'])
        self.assertEqual(supervisor.signals[0][1],'sender-0')
        session.arm()  # permanent deduplication after cleanup
        self.assertEqual(len(supervisor.launches),1)

    def test_sender_daemon_first_nonzero_exit_immediately_reaps_live_peer(self):
        from multiserver_pipeline_runtime_test import FakeSupervisor
        config=copy.deepcopy(self.config);config['jobs'].append(copy.deepcopy(config['jobs'][0]))
        class Supervisor(FakeSupervisor):
            def launch(inner,*args):
                report=super(Supervisor,inner).launch(*args)
                if args[1]=='sender-0':
                    inner.reports[(report['identity']['owner']['generation'],'sender-0')].update(state='EXITED',returncode=7)
                return report
            def close(inner):
                if any(r['state']=='RUNNING' for r in inner.reports.values()):
                    raise AssertionError('unreaped sender child')
        Path(config['root']).mkdir(mode=0o700)
        supervisor=Supervisor();private=self.peer.PrivateRoot(self.peer.sender_root(config,self.context))
        try:private.write('launch.json',dict(config=config,context=self.context,token='fixed'))
        finally:private.close()
        with patch.object(self.peer,'ProcessSupervisor',return_value=supervisor), \
             patch.object(self.peer,'_read_process',return_value={'pid':123}), \
             patch.object(self.peer.time,'sleep',side_effect=AssertionError('waited despite failed station')):
            self.peer.sender_daemon(config,self.context,'fixed')
        self.assertTrue(any(role=='sender-1' for _,role,_ in supervisor.signals))

    @unittest.skipUnless(sys.platform=='linux','detached peer lifecycle requires Linux /proc')
    def test_real_sender_daemon_reconnect_does_not_launch_twice_and_retires(self):
        utc=datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=3)
        utc=utc.replace(microsecond=0)
        context=copy.deepcopy(self.context);context['observation']['utc_start']=utc.strftime('%Y-%m-%d-%H:%M:%S')
        cfg=json.loads(self.config_path.read_text());cfg['time']['start_utc']=context['observation']['utc_start']
        epoch_start=utc.replace(month=1 if utc.month<=6 else 7,day=1,hour=0,minute=0,second=0)
        cfg['time'].update(reference_epoch=(utc.year-2000)*2+(utc.month>6),start_seconds=int((utc-epoch_start).total_seconds()))
        self.config_path.write_text(json.dumps(cfg));self.config['jobs'][0]['config_sha256']=hashlib.sha256(self.config_path.read_bytes()).hexdigest()
        self.binary.write_text('#!/usr/bin/python3\nimport json\nprint(json.dumps({"sent_packets":2048}))\n')
        self.config['binary']['sha256']=hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.config['lifetime_seconds']=10
        path=self.root/'peer.json';path.write_text(json.dumps(self.config));path.chmod(0o600)
        def call(operation):
            command=dict(schema_version=1,operation=operation,server_id='qtp1',gpu_id=0,context=context)
            result=subprocess.run([sys.executable,'-B',str(self.peer.__file__),'--deployment',str(path)],
                input=json.dumps(command),capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads(result.stdout)
        call('arm');first=call('collect')['senders'][0]['identity']
        call('arm');second=call('collect')['senders'][0]['identity']
        self.assertEqual(first,second)
        self.assertTrue(call('cleanup')['sender_processes_gone'])
        self.assertTrue(call('retire')['host_supervisors_gone'])

    @unittest.skipUnless(sys.platform=='linux','retained B2 requires Linux /proc')
    def test_real_sender_child_reaped_without_adoption_or_pid_kill(self):
        from scripts.multiserver.processes import ProcessSupervisor
        self.binary.write_text('#!/usr/bin/python3\nimport json\nprint(json.dumps({"sent_packets":2048}))\n')
        self.config['binary']['sha256']=hashlib.sha256(self.binary.read_bytes()).hexdigest()
        supervisor=ProcessSupervisor(str(self.root/'processes'))
        session=self.peer.SenderSession(self.config,self.context,supervisor)
        try:
            session.arm()
            import time
            limit=time.monotonic()+2
            while session.collect()['senders'][0]['state']=='RUNNING' and time.monotonic()<limit:
                time.sleep(0.01)
            self.assertEqual(session.collect()['senders'][0]['stats']['sent_packets'],2048)
            self.assertTrue(session.cleanup()['sender_processes_gone'])
        finally:
            session.cleanup();supervisor.close()


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--host-peer':host_peer(sys.argv[2])
    else:unittest.main()
