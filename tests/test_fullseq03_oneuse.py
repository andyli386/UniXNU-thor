#!/usr/bin/env python3
"""Guard FULLSEQ03 one-shot identity and never reuse old consumed images."""
import importlib.util, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/"tools/thorctl"
def load(name):
    import sys
    sys.path.insert(0,str(ROOT))
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+".py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
class GateTests(unittest.TestCase):
    def test_run_id_and_source_pinning(self):
        for name in ("verify_fullseq03","verify_fullseq03_offline"):
            module=load(name)
            self.assertEqual(module.RUN,"thor-20261027-ios-full-seq03")
            self.assertEqual(module.FD,"Build/thorEntry09/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd")
            source=(ROOT/(name+".py")).read_text()
            self.assertIn("7024292b456addd3bad0b0455b563f321468aeb0",source)
            self.assertIn("PASS_FULLSEQ03_HOST_SUITES_AND_SOURCE_IDENTITIES",source)
            self.assertIn("cases[26]",source)
            self.assertIn("cases[27]",source)
            self.assertNotIn("original_failure =",source)
    def test_package_is_new_run_and_binds_public_source(self):
        source=(ROOT/"fullseq03_pack.py").read_text()
        self.assertIn("thor-fullseq03-one-use-boot-package-v1",source)
        self.assertIn("thor-fullseq03-public-preflight-v1",source)
        self.assertIn("private_seed()",source)
        self.assertIn("not out.exists()",source)
if __name__=="__main__":unittest.main()
