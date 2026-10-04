"""Real loopback listener ownership; no game enumeration, capture or inputs."""
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
import socket
from threading import Thread
import unittest
from unittest.mock import Mock,patch
from urllib.parse import urlsplit
from urllib.request import urlopen

from bsgo_offline.easy_launcher import main
from bsgo_offline.observer_ui import make_server,_DashboardHTTPServer
from test_trace import scratch_directory


class ObserverBindingTests(unittest.TestCase):
    def test_platform_reuse_policy_preserves_non_windows_behavior(self):
        self.assertEqual(_DashboardHTTPServer.allow_reuse_address,
            False if os.name=="nt" else ThreadingHTTPServer.allow_reuse_address)
        self.assertEqual(_DashboardHTTPServer.allow_reuse_port,
            False if os.name=="nt" else getattr(ThreadingHTTPServer,"allow_reuse_port",False))

    @unittest.skipUnless(os.name=="nt","actual Windows socket regression")
    def test_windows_rejects_duplicate_bind_beside_legacy_reuse_listener(self):
        with scratch_directory() as directory:
            legacy=ThreadingHTTPServer(("127.0.0.1",0),BaseHTTPRequestHandler)
            try:
                self.assertTrue(legacy.allow_reuse_address)
                with self.assertRaises(OSError) as raised:
                    make_server(directory,legacy.server_port)
                self.assertEqual(raised.exception.winerror,10048)
                alternative=make_server(directory,0)
                try:
                    self.assertNotEqual(alternative.server_port,legacy.server_port)
                    self.assertEqual(alternative.socket.getsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR),0)
                    self.assertEqual(alternative.socket.getsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE),1)
                finally:
                    alternative.server_close()
            finally:
                legacy.server_close()

    def test_actual_occupied_listener_makes_launcher_choose_and_serve_alternate_port(self):
        with scratch_directory() as directory:
            legacy=ThreadingHTTPServer(("127.0.0.1",0),BaseHTTPRequestHandler)
            launcher=Mock(instance_id="a"*32)
            launcher.close.return_value=True
            launcher.startup_snapshot.return_value={"action_control":{"enabled":False},
                "instance_id":"a"*32,"service_kind":"manual_assist_launcher"}
            responses=[]
            original_join=Thread.join
            def join(thread,timeout=None):
                if thread.name=="manual-dashboard-http" and timeout==.5:
                    raise KeyboardInterrupt()
                return original_join(thread,timeout)
            def browser(url):
                self.assertNotEqual(urlsplit(url).port,legacy.server_port)
                with urlopen(url+"api/startup",timeout=2.) as response:
                    responses.append(json.loads(response.read()))
                return True
            try:
                with patch("sys.argv",["easy_launcher","--workspace",str(directory),"--port",str(legacy.server_port)]),\
                        patch("bsgo_offline.easy_launcher.compatible_listener",return_value=None),\
                        patch("bsgo_offline.easy_launcher.WorkspaceLease") as lease,\
                        patch("bsgo_offline.easy_launcher.EasyLauncher",return_value=launcher),\
                        patch("bsgo_offline.easy_launcher.webbrowser.open",side_effect=browser),\
                        patch.object(Thread,"join",join),redirect_stdout(io.StringIO()):
                    lease.return_value.acquire.return_value=True
                    self.assertEqual(main(),0)
                    launcher.retry_connection.assert_called_once()
                self.assertEqual(responses[0]["instance_id"],"a"*32)
                self.assertEqual(responses[0]["service_kind"],"manual_assist_launcher")
                # The occupied legacy fixture is still ours and still bound;
                # normal launcher startup never closed or killed that listener.
                self.assertEqual(legacy.socket.getsockname()[1],legacy.server_port)
            finally:
                legacy.server_close()


if __name__=="__main__":unittest.main()
