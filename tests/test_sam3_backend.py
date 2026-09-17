import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from backend.sam3_bridge import Sam3Bridge, Sam3BridgeError
from backend.sam3_worker import Sam3Worker, WorkerError, normalize_prompt


class FakePredictor:
    def __init__(self):
        self.requests = []

    def handle_request(self, **request):
        self.requests.append(request)
        if request["type"] == "start_session":
            return {"started": request["session_id"]}
        return {"accepted": request["type"]}

    def handle_stream_request(self, **request):
        self.requests.append(request)
        for index in range(5):
            yield {"frame_index": index, "mask": [index]}


class DirectPredictor:
    def __init__(self):
        self.prompts = []

    def start_session(self, session_id, resource_path):
        return {"started": session_id, "resource": resource_path}

    def add_prompt(self, **arguments):
        self.prompts.append(arguments)
        return {"accepted": True}

    def close_session(self, session_id):
        return {"closed": session_id}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.predictor = FakePredictor()
        self.worker = Sam3Worker(predictor_factory=lambda config: self.predictor)

    def test_normalization_and_prompt_translation(self):
        self.assertEqual(normalize_prompt({"type": "point", "point": [0.25, 1], "label": "negative"}), {"type": "point", "points": [[0.25, 1.0]], "point_labels": [0]})
        box = normalize_prompt({"type": "box", "box": [0, 0.1, 1, 0.9], "label": "positive"})
        self.assertEqual(box["bounding_boxes"], [[0.0, 0.1, 1.0, 0.8]])
        self.assertEqual(box["bounding_box_labels"], [1])
        self.assertEqual(normalize_prompt({"type": "text", "text": "person"})["text"], "person")
        example = normalize_prompt({"type": "example", "box": [0.1, 0.1, 0.2, 0.2], "label": 0})
        self.assertEqual(example["bounding_boxes"], [[0.1, 0.1, 0.1, 0.1]])
        self.assertEqual(example["bounding_box_labels"], [0])
        points = normalize_prompt({
            "type": "point",
            "points": [[0.1, 0.2], [0.3, 0.4]],
            "labels": ["positive", "negative"],
        })
        self.assertEqual(points["point_labels"], [1, 0])
        for prompt in (
            {"type": "point", "point": [-0.1, 0.2]},
            {"type": "point", "point": [float("nan"), 0.2]},
            {"type": "box", "box": [0, 0, 0, 1]},
            {"type": "text", "text": ""},
        ):
            with self.assertRaises(WorkerError):
                normalize_prompt(prompt)

    def test_lifecycle_and_official_request_shape(self):
        self.worker.handle("start_session", {"session_id": "s1", "video_path": "clip.mp4"})
        self.worker.handle("add_prompt", {"session_id": "s1", "frame_index": 12, "obj_id": 3, "prompt": {"type": "point", "point": [0.2, 0.3], "label": "positive"}})
        self.worker.handle("add_prompt", {"session_id": "s1", "frame_index": 12, "prompt": {"type": "text", "text": "person"}})
        self.worker.handle("remove_object", {"session_id": "s1", "obj_id": 3})
        self.worker.handle("reset_session", {"session_id": "s1"})
        self.worker.handle("close_session", {"session_id": "s1"})
        self.assertEqual(self.predictor.requests[0]["type"], "start_session")
        prompt_request = self.predictor.requests[1]
        self.assertEqual(prompt_request["type"], "add_prompt")
        self.assertEqual(prompt_request["points"], [[0.2, 0.3]])
        self.assertEqual(prompt_request["point_labels"], [1])
        self.assertEqual(self.worker.health()["sessions"], 0)

    def test_health_does_not_import_or_build_the_model(self):
        worker = Sam3Worker(
            predictor_factory=lambda config: (_ for _ in ()).throw(
                AssertionError("health must not build the predictor")
            )
        )
        with mock.patch(
            "backend.sam3_worker.importlib.import_module",
            side_effect=AssertionError("health must not import SAM 3.1"),
        ):
            health = worker.health()
        self.assertTrue(health["ok"])
        self.assertFalse(health["model_loaded"])

    def test_start_session_reports_model_and_video_phases(self):
        events = []
        self.worker.handle(
            "start_session",
            {"session_id": "s1", "video_path": "clip.mp4"},
            events.append,
        )
        self.assertEqual(
            [event["phase"] for event in events],
            ["loading_model", "opening_video", "ready"],
        )

    def test_run_initializes_predictor_on_process_main_thread(self):
        predictor = FakePredictor()
        factory_threads = []

        def factory(config):
            factory_threads.append(threading.get_ident())
            return predictor

        worker = Sam3Worker(predictor_factory=factory)
        requests = "\n".join([
            json.dumps({
                "id": "start",
                "command": "start_session",
                "payload": {"session_id": "s1", "video_path": "clip.mp4"},
            }),
            json.dumps({"id": "stop", "command": "shutdown", "payload": {}}),
            "",
        ])
        worker.run(io.StringIO(requests), io.StringIO())
        self.assertEqual(factory_threads, [threading.get_ident()])

    def test_predictor_adapter_filters_unsupported_optional_session_kwargs(self):
        calls = []

        class Model:
            def init_state(self, resource_path, offload_video_to_cpu=False):
                calls.append((resource_path, offload_video_to_cpu))
                return {"ready": True}

        class Predictor:
            model = Model()

        predictor = Sam3Worker._adapt_predictor(Predictor())
        result = predictor.model.init_state(
            resource_path="frames",
            offload_video_to_cpu=True,
            offload_state_to_cpu=False,
            async_loading_frames=True,
        )
        self.assertEqual(result, {"ready": True})
        self.assertEqual(calls, [("frames", True)])

    def test_internal_attribute_errors_are_not_mislabeled_as_unsupported(self):
        class BrokenPredictor:
            def handle_request(self, **request):
                raise AttributeError("video loader state is missing")

        worker = Sam3Worker(predictor_factory=lambda config: BrokenPredictor())
        with self.assertRaises(WorkerError) as raised:
            worker.handle(
                "start_session",
                {"session_id": "s1", "video_path": "frames"},
            )
        self.assertEqual(raised.exception.code, "upstream_error")
        self.assertIn("video loader state is missing", str(raised.exception))

    def test_internal_not_implemented_errors_preserve_upstream_detail(self):
        class BrokenPredictor:
            def handle_request(self, **request):
                raise NotImplementedError("frame loader backend is unavailable")

        worker = Sam3Worker(predictor_factory=lambda config: BrokenPredictor())
        with self.assertRaises(WorkerError) as raised:
            worker.handle(
                "start_session",
                {"session_id": "s1", "video_path": "frames"},
            )
        self.assertEqual(raised.exception.code, "upstream_error")
        self.assertIn("frame loader backend is unavailable", str(raised.exception))

    def test_propagation_stream_and_cancellation(self):
        self.worker.handle("start_session", {"session_id": "s1", "video_path": "clip.mp4"})
        events = []
        result = self.worker.propagate({"session_id": "s1"}, events.append, threading.Event())
        self.assertEqual(result, {"cancelled": False, "events": 5})
        self.assertEqual(len(events), 5)

        cancel = threading.Event()
        cancel.set()
        result = self.worker.propagate({"session_id": "s1"}, events.append, cancel)
        self.assertTrue(result["cancelled"])

    def test_unknown_prompt_is_structured_capability_error(self):
        with self.assertRaises(WorkerError) as raised:
            normalize_prompt({"type": "polygon", "points": []})
        self.assertEqual(raised.exception.code, "unsupported_capability")

    def test_official_add_prompt_api_receives_xywh_and_relative_coordinates(self):
        predictor = DirectPredictor()
        worker = Sam3Worker(predictor_factory=lambda config: predictor)
        worker.handle("start_session", {"session_id": "s1", "video_path": "frames"})
        worker.handle("add_prompt", {"session_id": "s1", "frame_index": 4, "obj_id": 7, "prompt": {"type": "box", "box": [0.1, 0.2, 0.8, 0.9], "label": "positive"}})
        arguments = predictor.prompts[0]
        self.assertEqual(arguments["frame_idx"], 4)
        self.assertEqual(arguments["bounding_boxes"], [[0.1, 0.2, 0.7000000000000001, 0.7]])
        self.assertEqual(arguments["bounding_box_labels"], [1])
        self.assertTrue(arguments["rel_coordinates"])

        worker.handle("add_prompt", {
            "session_id": "s1",
            "frame_index": 4,
            "prompt": {
                "type": "example",
                "examples": [
                    {"box": [0.1, 0.2, 0.8, 0.9], "label": "positive"},
                    {"box": [0.2, 0.3, 0.4, 0.5], "label": "negative"},
                ],
            },
        })
        arguments = predictor.prompts[1]
        self.assertEqual(arguments["bounding_box_labels"], [1, 0])
        self.assertEqual(len(arguments["bounding_boxes"]), 2)

    def test_large_masks_are_written_to_shared_mask_root(self):
        class FakeArray:
            shape = (5000,)
            dtype = "uint8"

            def tobytes(self):
                return b"m" * 5000

        class MaskPredictor(FakePredictor):
            def handle_request(self, **request):
                self.requests.append(request)
                return {"out_binary_masks": FakeArray()}

        predictor = MaskPredictor()
        with tempfile.TemporaryDirectory() as root:
            worker = Sam3Worker(predictor_factory=lambda config: predictor)
            worker.handle("start_session", {"session_id": "s1", "video_path": "frames", "mask_root": root})
            result = worker.handle("add_prompt", {"session_id": "s1", "prompt": {"type": "text", "text": "person"}})
            mask = result["out_binary_masks"]
            self.assertEqual(mask["encoding"], "raw")
            self.assertEqual(Path(mask["path"]).read_bytes(), b"m" * 5000)

    def test_predictor_operations_are_serialized(self):
        class SerialPredictor(FakePredictor):
            def __init__(self):
                super().__init__()
                self.guard = threading.Lock()
                self.active = 0
                self.max_active = 0

            def handle_request(self, **request):
                with self.guard:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    time.sleep(0.02)
                    return super().handle_request(**request)
                finally:
                    with self.guard:
                        self.active -= 1

        predictor = SerialPredictor()
        worker = Sam3Worker(predictor_factory=lambda config: predictor)
        worker.handle("start_session", {"session_id": "s1", "video_path": "frames"})
        threads = [
            threading.Thread(
                target=lambda: worker.handle("reset_session", {"session_id": "s1"})
            )
            for _ in range(3)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertEqual(predictor.max_active, 1)


FAKE_CHILD = r'''
import json, sys, time, threading
active = {}
write_lock = threading.Lock()
stop = threading.Event()
def send(message):
    with write_lock:
        print(json.dumps(message), flush=True)
def dispatch(request):
    rid = request["id"]
    command = request["command"]
    payload = request.get("payload", {})
    if command == "health":
        send({"id": rid, "ok": True, "result": {"ok": True, "fake": True}})
    elif command == "cancel":
        active[payload.get("request_id")] = True
        send({"id": rid, "ok": True, "result": {"cancelled": True}})
    elif command == "propagate":
        active[rid] = False
        for index in range(20):
            if active[rid]:
                break
            send({"id": rid, "event": "result", "result": {"frame": index}})
            time.sleep(0.01)
        send({"id": rid, "ok": True, "result": {"cancelled": active[rid]}})
    elif command == "shutdown":
        send({"id": rid, "ok": True, "result": {"shutdown": True}})
        stop.set()
    else:
        send({"id": rid, "ok": True, "result": {"command": command, "payload": payload}})
for line in sys.stdin:
    if stop.is_set():
        break
    threading.Thread(target=dispatch, args=(json.loads(line),), daemon=True).start()
'''


class BridgeTests(unittest.TestCase):
    def make_child(self, source):
        handle = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
        handle.write(textwrap.dedent(source))
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return [sys.executable, "-u", handle.name]

    def test_protocol_ids_streaming_cancellation_and_clean_shutdown(self):
        bridge = Sam3Bridge(self.make_child(FAKE_CHILD), startup_timeout=2, request_timeout=2)
        self.addCleanup(bridge.close)
        self.assertTrue(bridge.start()["fake"])
        events = []
        request_id = "propagate-test"
        result_holder = []
        thread = threading.Thread(target=lambda: result_holder.append(bridge.propagate({}, on_event=events.append, request_id=request_id)))
        thread.start()
        time.sleep(0.04)
        bridge.cancel(request_id)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result_holder[0]["cancelled"])
        self.assertGreater(len(events), 0)
        bridge.close()
        self.assertFalse(bridge.is_running)

    def test_startup_timeout_and_crash_are_structured(self):
        slow = self.make_child("import time; time.sleep(2)")
        bridge = Sam3Bridge(slow, startup_timeout=0.05)
        with self.assertRaises(Sam3BridgeError) as raised:
            bridge.start()
        self.assertEqual(raised.exception.code, "startup_timeout")
        bridge.close()

        crashed = self.make_child("raise SystemExit(3)")
        bridge = Sam3Bridge(crashed, startup_timeout=1)
        with self.assertRaises(Sam3BridgeError) as raised:
            bridge.start()
        self.assertIn(raised.exception.code, {"bridge_crashed", "startup_timeout"})
        bridge.close()

    def test_timeout_reports_last_worker_phase(self):
        phased = self.make_child(r'''
import json, sys, time
request = json.loads(sys.stdin.readline())
print(json.dumps({"id": request["id"], "event": "result", "result": {"phase": "loading_model"}}), flush=True)
time.sleep(2)
''')
        bridge = Sam3Bridge(phased, startup_timeout=1.0)
        with self.assertRaises(Sam3BridgeError) as raised:
            bridge.start()
        self.assertEqual(raised.exception.code, "startup_timeout")
        self.assertIn("loading model", str(raised.exception))
        self.assertEqual(raised.exception.details["phase"], "loading_model")
        bridge.close()

    def test_token_is_not_echoed(self):
        token = "hf_secret_test_value"
        old = os.environ.get("HF_TOKEN")
        os.environ["HF_TOKEN"] = token
        try:
            worker = Sam3Worker(predictor_factory=lambda config: (_ for _ in ()).throw(RuntimeError(token)))
            with self.assertRaises(WorkerError) as raised:
                worker.handle("start_session", {"session_id": "s1", "video_path": "clip.mp4"})
            error = worker._error_payload(raised.exception)
            serialized = json.dumps(error)
            self.assertNotIn(token, serialized)
            self.assertNotIn(token, json.dumps(worker.health()))
        finally:
            if old is None:
                os.environ.pop("HF_TOKEN", None)
            else:
                os.environ["HF_TOKEN"] = old

    def test_worker_rejects_token_ingress(self):
        worker = Sam3Worker(predictor_factory=lambda config: FakePredictor())
        with self.assertRaises(WorkerError) as raised:
            worker.handle("start_session", {
                "session_id": "s1", "video_path": "clip.mp4",
                "hf_token": "hf_must_not_reach_inference",
            })
        self.assertEqual(raised.exception.code, "credential_error")
        self.assertNotIn("hf_must_not_reach_inference", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
