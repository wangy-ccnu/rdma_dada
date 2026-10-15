"""Opt-in real Linux no-input readiness/cleanup gate for C1 hooks.

Server owner supplies --deployment ABSOLUTE_JSON from I1's task-owned fixture.
Without that explicit input only the native case SKIPS; the portable publisher
tests still run and never touch rings/GPU/RDMA.
The deployment must reference an installed finite bundle and a fresh run_dir.
Finite data/EOD, partial tails and Station participation remain I1 sender gates.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.multiserver.bundle import _read_json
from scripts.multiserver.ownership import HostJournal
from scripts.multiserver.processes import ProcessSupervisor
from scripts.multiserver.pipeline_runtime import PipelineRuntime

DEPLOYMENT = None


class PortableLifecyclePublisherTest(unittest.TestCase):
    """Actual shared POSIX publisher, without PSRDADA/CUDA or product binaries."""
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('c++')
        if compiler is None:
            raise unittest.SkipTest('portable C++ compiler unavailable')
        cls.temp=tempfile.TemporaryDirectory(prefix='ms07c-publisher-build-')
        cls.addClassCleanup(cls.temp.cleanup)
        root=Path(cls.temp.name).resolve()
        source=root/'publisher.cpp'
        source.write_text('''#include "rdma_dada/control/lifecycle_json.h"
#include <string>
#include <unistd.h>
int main(int argc, char** argv) {
    if (argc != 2 && argc != 3) return 2;
    const std::string path(argv[1]);
    const std::string temporary=path+".tmp."+std::to_string(getpid());
    if (argc == 3 && symlink(argv[2], temporary.c_str()) != 0) return 2;
    const bool ok=rdma_dada::control::PublishLifecycleJson(path,"{\\\"value\\\":1}\\n");
    if (argc == 3) unlink(temporary.c_str());
    return ok ? 0 : 1;
}
''')
        cls.binary=root/'publisher'
        include=Path(__file__).resolve().parents[1]/'include'
        cls.build=subprocess.run([compiler,'-std=c++11','-I'+str(include),str(source),'-o',str(cls.binary)],
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)

    def setUp(self):
        self.assertEqual(self.build.returncode,0,self.build.stderr)
        temporary=tempfile.TemporaryDirectory(prefix='ms07c-publisher-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.path=self.root/'record.json'

    def publish(self,path=None,*extra):
        return subprocess.run([str(self.binary),str(self.path if path is None else path)]+list(extra),
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5).returncode

    def test_publish_private_complete_record_and_remove_temporary(self):
        self.assertEqual(self.publish(),0)
        self.assertEqual(self.path.read_bytes(),b'{"value":1}\n')
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.path.stat().st_nlink,1)
        self.assertEqual(list(self.root.iterdir()),[self.path])

    def test_existing_record_is_not_replaced(self):
        self.path.write_bytes(b'original')
        self.assertEqual(self.publish(),1)
        self.assertEqual(self.path.read_bytes(),b'original')
        self.assertEqual(list(self.root.iterdir()),[self.path])

    def test_destination_symlink_and_target_are_preserved(self):
        victim=self.root/'victim';victim.write_bytes(b'foreign')
        self.path.symlink_to(victim)
        self.assertEqual(self.publish(),1)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(victim.read_bytes(),b'foreign')
        self.assertFalse(list(self.root.glob('record.json.tmp.*')))

    def test_temporary_symlink_is_never_followed(self):
        victim=self.root/'victim';victim.write_bytes(b'foreign')
        self.assertEqual(self.publish(self.path,str(victim)),1)
        self.assertEqual(victim.read_bytes(),b'foreign')
        self.assertFalse(self.path.exists())

    def test_optional_empty_path_has_no_effect(self):
        self.assertEqual(self.publish(''),0)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_missing_parent_fails_without_publication(self):
        self.assertEqual(self.publish(self.root/'missing'/'record.json'),1)
        self.assertEqual(list(self.root.iterdir()),[])


@unittest.skipUnless(sys.platform=='linux' and DEPLOYMENT,
                     'requires explicit Linux --deployment; real PSRDADA/CUDA gate NOT RUN')
class NativeLifecycleHooksTest(unittest.TestCase):
    def test_ready_without_input_and_no_fabricated_completion(self):
        config=_read_json(DEPLOYMENT)
        instance=config['instances'][0]
        owner={k:instance[k] for k in ('run_id','bundle_id','server_id','gpu_id','observation_sha256')}
        owner['generation']='hooks-'+uuid.uuid4().hex
        root=Path(config['root'])
        root.mkdir(mode=0o700,parents=True,exist_ok=True)
        journal=HostJournal(root/'hooks-journal.sqlite')
        supervisor=ProcessSupervisor(str(root/'hooks-processes'))
        runtime=None
        try:
            journal.claim(owner,instance['resources'])
            runtime=PipelineRuntime(config,supervisor,journal=journal)
            ready=runtime.prepare(owner,instance['bundle_path'])
            self.assertEqual(ready['state'],'READY',ready)
            self.assertFalse(any(ready['evidence'].values()))
            directory=Path(instance['resources']['run_dir'])
            records={role:json.loads((directory/(role+'.ready')).read_text())
                     for role in ('gpu','unpack','receiver')}
            self.assertEqual(len({r['config_id'] for r in records.values()}),1)
            self.assertEqual(len({r['geometry_id'] for r in records.values()}),1)
            for role,record in records.items():
                self.assertGreater(record['pid'],0)
                self.assertGreater(record['input_key'],0)
                self.assertFalse((directory/(role+'.result')).exists())
            # A manual no-input stop must never turn elapsed time/exit0 into PASS.
            stopped=runtime.stop(owner,'graceful',config['stop_timeout_seconds'])
            self.assertEqual(stopped['state'],'INTERRUPTED',stopped)
            self.assertFalse(stopped['evidence']['target_data_complete'])
            self.assertEqual(stopped['cleanup'],'CONFIRMED',stopped)
            journal.release(owner,stopped['cleanup_evidence'])
        finally:
            if runtime is not None:
                runtime.close()
            supervisor.close()  # Refuses to discard any unreaped Popen child.
            journal.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--deployment',type=Path)
    args,remaining=parser.parse_known_args()
    if args.deployment:
        if sys.platform!='linux' or not args.deployment.is_absolute():
            parser.error('explicit canonical Linux deployment path required')
        DEPLOYMENT=args.deployment
        NativeLifecycleHooksTest.__unittest_skip__=False
    unittest.main(argv=[sys.argv[0]]+remaining)
