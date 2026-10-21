import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from apitest.public import public_data, export_site
from apitest.evaluation import build_overview

NOW=2000000000
SECRET="private-test-value-not-for-public"


def fixture():
    return [{"id":"private-run","meta":{"preset":"short","prompt_chars":20,"system":False,"max_tokens":64,
        "stream":True,"concurrency":1,"cache_mode":"cold","order":"interleaved",
        "api_key":SECRET,"pricing":{"private@low/model":{"input":999}},"budget":{"accounted_cost":900}},
        "results":[{"target":"private@low","model":"model","provider":"openai","effort":"low","index":0,
        "attempt":1,"started_at":NOW-120,"ok":True,"stream":True,"text_chars":80,"ttft_text":.8,
        "total":2,"gen_time":1,"tps":32,"tps_eligible":True,"output_tokens":32,"reasoning_tokens":0,
        "max_gap":.1,"single_chunk":False,"chars_per_second":80,"text":SECRET,"error":SECRET,
        "cost":99,"currency":"PRIVATE","budget_charge":999,"base_url":"https://private.example.test"}]}]


class PublicExportTests(unittest.TestCase):
    def test_private_fields_are_excluded_from_every_window_and_profile(self):
        original=fixture();before=copy.deepcopy(original)
        data=public_data(original,"private",now=NOW)
        serialized=json.dumps(data)
        for secret in (SECRET,"private-run","private@low","private.example.test","budget_charge","accounted_cost",'"cost"','"currency"','"pricing"','"api_key"'):
            self.assertNotIn(secret,serialized)
        self.assertEqual(set(data["windows"]),{"1","6","24","72","168"})
        self.assertEqual(data["windows"]["24"]["interactive"]["variants"][0]["metrics"]["ttfa_p50"],.8)
        self.assertEqual(original,before)

    def test_stats_match_the_existing_evaluator(self):
        rows=fixture()
        private=build_overview(rows,now=NOW,hours=24)["variants"][0]
        public=public_data(rows,"private",now=NOW)["windows"]["24"]["interactive"]["variants"][0]
        for key,value in public["metrics"].items():self.assertEqual(value,private["metrics"][key])
        self.assertEqual(public["assessment"],private["assessment"])

    def test_windows_are_computed_from_requests_not_averaged_medians(self):
        rows=fixture();older=copy.deepcopy(rows[0]["results"][0]);older.update(index=1,started_at=NOW-7200,ttft_text=10)
        rows[0]["results"].append(older)
        data=public_data(rows,"private",now=NOW)
        self.assertEqual(data["windows"]["1"]["interactive"]["variants"][0]["metrics"]["n"],1)
        self.assertEqual(data["windows"]["24"]["interactive"]["variants"][0]["metrics"]["n"],2)

    def test_other_endpoints_and_unselected_models_do_not_leak(self):
        rows=fixture();other=copy.deepcopy(rows[0]["results"][0]);other.update(target="another@low",model="hidden-model")
        rows[0]["results"].append(other)
        data=public_data(rows,"private",["model"],now=NOW)
        self.assertNotIn("hidden-model",json.dumps(data))
        with self.assertRaises(ValueError):public_data(rows,"misspelled",now=NOW)

    def test_repeat_export_does_not_change_files_or_generation_time(self):
        with tempfile.TemporaryDirectory() as directory:
            first=export_site(directory,fixture(),"private",now=NOW)
            before=(Path(directory)/"data.json").read_bytes()
            again=export_site(directory,fixture(),"private",now=NOW+1)
            self.assertTrue(first["changed_files"])
            self.assertEqual(again["changed_files"],[])
            self.assertEqual((Path(directory)/"data.json").read_bytes(),before)

    def test_expired_samples_update_the_time_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            export_site(directory,fixture(),"private",now=NOW)
            changed=export_site(directory,fixture(),"private",now=NOW+3600)
            self.assertIn("data.json",changed["changed_files"])
            saved=json.loads((Path(directory)/"data.json").read_text())
            self.assertEqual(saved["windows"]["1"]["interactive"]["variants"],[])

    def test_export_is_static_and_has_no_admin_page_or_session_token(self):
        with tempfile.TemporaryDirectory() as directory:
            export_site(directory,fixture(),"private",title="实验室 <模型>",now=NOW)
            page=(Path(directory)/"index.html").read_text()
            self.assertIn('class="page active"',page)
            self.assertNotIn('data-page="run"',page)
            self.assertNotIn('apitest-token',page)
            self.assertNotIn('ov-policy',page)
            self.assertIn('实验室 &lt;模型&gt;',page)
            self.assertNotIn('value="all"',page)
            self.assertNotIn(SECRET,page)

    def test_unmanaged_directories_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/"out";out.mkdir();(out/"important.txt").write_text("keep")
            with self.assertRaises(ValueError):export_site(out,fixture(),"private",now=NOW)
            self.assertEqual((out/"important.txt").read_text(),"keep")
            fresh=root/"fresh";export_site(fresh,fixture(),"private",now=NOW)
            (fresh/"data.json").unlink();(fresh/"data.json").symlink_to(out/"important.txt")
            with self.assertRaises(ValueError):export_site(fresh,fixture(),"private",now=NOW)
            self.assertEqual((out/"important.txt").read_text(),"keep")


class PublisherTests(unittest.TestCase):
    def test_two_publishes_create_only_one_data_commit(self):
        source=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);remote=root/"remote.git";site=root/"site";history=root/"history.jsonl"
            def run(*args,cwd=None):return subprocess.run(args,cwd=cwd,check=True,capture_output=True,text=True)
            run("git","init","--bare",str(remote));run("git","init","-b","main",str(site))
            run("git","config","user.name","Test",cwd=site);run("git","config","user.email","test@example.test",cwd=site)
            run("git","remote","add","origin",str(remote),cwd=site)
            data=fixture();import time
            data[0]["results"][0]["started_at"]=time.time()-120
            history.write_text(json.dumps(data[0])+"\n")
            command=[sys.executable,str(source/"scripts/publish_status.py"),"--site-repo",str(site),"--endpoint","private","--history",str(history),"--once"]
            first=run(*command,cwd=source);second=run(*command,cwd=source)
            self.assertEqual(json.loads(first.stdout)["status"],"published")
            self.assertEqual(json.loads(second.stdout)["status"],"unchanged")
            self.assertEqual(run("git","rev-list","--count","HEAD",cwd=site).stdout.strip(),"1")
            self.assertEqual(run("git","status","--porcelain",cwd=site).stdout,"")
