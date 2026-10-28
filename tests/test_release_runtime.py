"""Run the offline-installed wheel outside source with the unchanged node protocol."""

import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from _release_fixture import install_runtime, shared_release
from project_tools.configure import generate


class ReleaseRuntimeTests(unittest.TestCase):
    def test_installed_center_and_standalone_reporter_support_both_endpoints_and_dcgm(
        self,
    ):
        # Synthetic DCGM-exporter wire fixture, not a claim of hardware support.
        metrics = "\n".join(
            f'{field}{{gpu="0",UUID="GPU-fixture",modelName="NVIDIA fixture"}} {value}'
            for field, value in (
                ("DCGM_FI_DEV_GPU_UTIL", 40),
                ("DCGM_FI_DEV_FB_USED", 1024),
                ("DCGM_FI_DEV_FB_TOTAL", 4096),
                ("DCGM_FI_PROF_SM_ACTIVE", 0.75),
                ("DCGM_FI_PROF_PIPE_TENSOR_ACTIVE", 0),
                ("DCGM_FI_PROF_PCIE_RX_BYTES", 1048576),
                ("DCGM_FI_DEV_MEMORY_TEMP", 9223372036854775794),
                ("DCGM_FI_DEV_ECC_SBE_VOL_TOTAL", 0),
            )
        ).encode()

        class Exporter(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/metrics":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(metrics)))
                self.end_headers()
                self.wfile.write(metrics)

            def log_message(self, *_args):
                pass

        exporter = ThreadingHTTPServer(("127.0.0.1", 0), Exporter)
        exporter_thread = threading.Thread(target=exporter.serve_forever, daemon=True)
        exporter_thread.start()
        self.addCleanup(exporter.server_close)
        self.addCleanup(exporter_thread.join, 5)
        self.addCleanup(exporter.shutdown)
        gpu_settings = {
            "provider": "dcgm",
            "processes": "none",
            "url": f"http://127.0.0.1:{exporter.server_port}/metrics",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, runtime, private = (
                shared_release(),
                root / "runtime",
                root / "private",
            )
            python = install_runtime(runtime, release)
            # A leftover legacy source package must not shadow the installed wheel.
            (runtime / "monitor.py").write_text("raise RuntimeError('source shadow')\n")
            configuration = generate(
                private,
                [
                    {"name": "node-a", "address": "192.0.2.1", "groups": []},
                    {"name": "node-dcgm", "address": "192.0.2.2", "gpu": gpu_settings},
                ],
                "http://127.0.0.1:8080/api/agent/report",
            )
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in ("PYTHONPATH", "PYTHONHOME", "CLUSTERLENS_VIEWER_TOKEN")
            }
            database = root / "history.sqlite3"
            center = subprocess.Popen(
                [
                    str(python),
                    "-B",
                    "-I",
                    "-m",
                    "monitor",
                    "--config",
                    str(private / "server.json"),
                    "--port",
                    "0",
                    "--db",
                    str(database),
                ],
                cwd=runtime,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            lines = queue.Queue()

            def read_log():
                for line in center.stderr:
                    lines.put(line)
                lines.put(None)

            reader = threading.Thread(target=read_log, daemon=True)
            reader.start()
            try:
                while True:
                    line = lines.get(timeout=10)
                    self.assertIsNotNone(line, "released center exited before startup")
                    match = re.search(r"http://127\.0\.0\.1:\d+", line)
                    if match:
                        base = match[0]
                        break
                with urlopen(base + "/healthz", timeout=5) as response:
                    self.assertEqual(json.load(response), {"status": "ok"})
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + "/api/overview", timeout=5)
                self.assertEqual(error.exception.code, 401)
                error.exception.close()
                with urlopen(base, timeout=5) as response:
                    self.assertIn("ClusterLens", response.read().decode())
                agent_file = private / "agents/node-a.json"
                agent_config = json.loads(agent_file.read_text())
                agent_config["url"] = base + "/api/agent/report"
                agent_file.write_text(json.dumps(agent_config))
                # Use the released sender without collecting on the test machine.
                sender = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-S",
                        "-c",
                        (
                            "import json, runpy, sys, time, uuid; "
                            "agent=runpy.run_path(sys.argv[1]); "
                            "config=agent['load_reporter_config'](sys.argv[2]); "
                            "report={'version':1,'host':config['host'],'interval':30, "
                            "'report_id':str(uuid.uuid4()),'collected_at':time.time(), "
                            "'system':{'cpu_percent':0},'gpus':[]}; "
                            "result=agent['send_report'](config,report); "
                            "assert result['accepted'] is True; "
                            "config['url']=config['url'].replace('/api/agent/report','/api/node/report'); "
                            "duplicate=agent['send_report'](config,report); "
                            "assert duplicate['duplicate'] is True and duplicate['accepted'] is False"
                        ),
                        str(release / "agent.py"),
                        str(agent_file),
                    ],
                    cwd=runtime,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(sender.returncode, 0, sender.stderr)
                dcgm_file = private / "agents/node-dcgm.json"
                dcgm_config = json.loads(dcgm_file.read_text())
                dcgm_config["url"] = base + "/api/node/report"
                dcgm_file.write_text(json.dumps(dcgm_config))
                # Execute the shipped Python3.6+/stdlib file under -S, retaining
                # its actual config -> DCGM HTTP -> conversion -> sender path.
                # Only OS probes are synthetic; no GPU command or daemon runs.
                dcgm_sender = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-S",
                        "-c",
                        (
                            "import runpy,sys; ns=runpy.run_path(sys.argv[1]); "
                            "config=ns['load_reporter_config'](sys.argv[2]); "
                            "g=ns['make_report'].__globals__; "
                            "g['cpu_ticks']=lambda:(100,10); g['counters']=lambda:(0,0,0,0); "
                            "g['memory']=lambda:{'memory_percent':0}; g['disks']=lambda:[]; "
                            "g['read_text']=lambda p:'100 0' if p=='/proc/uptime' else ''; "
                            "report=ns['make_report'](config); "
                            "assert report['gpu_provider']=='dcgm'; "
                            "assert report['gpus'][0]['dcgm']['sm_active_percent']==75; "
                            "assert ns['send_report'](config,report)['accepted'] is True"
                        ),
                        str(release / "agent.py"),
                        str(dcgm_file),
                    ],
                    cwd=runtime,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(dcgm_sender.returncode, 0, dcgm_sender.stderr)
                request = Request(
                    base + "/api/overview",
                    headers={
                        "Authorization": "Bearer " + configuration["viewer_token"]
                    },
                )
                with urlopen(request, timeout=5) as response:
                    overview = json.load(response)
                node, dcgm_node = overview["hosts"]
                self.assertEqual(node["status"], "online")
                self.assertEqual(node["system"]["cpu_percent"], 0)
                self.assertIsNone(node["system"]["memory_percent"])
                self.assertEqual(dcgm_node["gpu_provider"], "dcgm")
                gpu = dcgm_node["gpus"][0]
                self.assertEqual(gpu["utilization"], 40)
                self.assertEqual(gpu["memory_percent"], 25)
                self.assertEqual(gpu["dcgm"]["tensor_active_percent"], 0)
                self.assertEqual(gpu["dcgm"]["pcie_rx_bytes_s"], 1048576)
                self.assertIsNone(gpu["dcgm"]["memory_temperature"])
                self.assertFalse(gpu["processes_available"])
                self.assertEqual(gpu["processes"], [])
                self.assertNotIn("token", json.dumps(overview))
                self.assertNotIn(gpu_settings["url"], json.dumps(overview))
            finally:
                center.terminate()
                try:
                    center.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    center.kill()
                    center.wait(timeout=5)
                    self.fail("released center did not stop on SIGTERM")
                finally:
                    reader.join(timeout=2)
                    center.stderr.close()
            self.assertEqual(center.returncode, 0)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 2
                )
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
