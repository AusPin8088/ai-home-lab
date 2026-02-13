import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from agent.main import parse_direct_action_command
from agent.main import parse_direct_action_plan
from agent.main import parse_command_payload


class ActionParserTests(unittest.TestCase):
    def test_turn_on_plug_2(self):
        action, outlet, detail = parse_direct_action_command("turn on plug 2")
        self.assertEqual(action, "turn_on")
        self.assertEqual(outlet, 2)
        self.assertIn("parsed", detail)

    def test_turn_off_outlet_4(self):
        action, outlet, _ = parse_direct_action_command("please switch off outlet 4 now")
        self.assertEqual(action, "turn_off")
        self.assertEqual(outlet, 4)

    def test_missing_outlet(self):
        action, outlet, detail = parse_direct_action_command("turn on the light")
        self.assertIsNone(action)
        self.assertIsNone(outlet)
        self.assertIn("plug number", detail)

    def test_missing_action(self):
        action, outlet, detail = parse_direct_action_command("plug 3")
        self.assertIsNone(action)
        self.assertEqual(outlet, 3)
        self.assertIn("no on/off intent", detail)

    def test_ambiguous_action(self):
        action, outlet, detail = parse_direct_action_command("turn on then turn off plug 2")
        self.assertIsNone(action)
        self.assertIsNone(outlet)
        self.assertIn("ambiguous", detail)

    def test_ambiguous_action_with_and_off_phrase(self):
        action, outlet, detail = parse_direct_action_command("can you turn on and off the plug 3")
        self.assertIsNone(action)
        self.assertIsNone(outlet)
        self.assertIn("ambiguous", detail)

    def test_multi_action_plan(self):
        steps, detail = parse_direct_action_plan("turn on plug 3 and 4 and turn off plug 2")
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["action"], "turn_on")
        self.assertEqual(steps[0]["outlet"], 3)
        self.assertEqual(steps[1]["action"], "turn_on")
        self.assertEqual(steps[1]["outlet"], 4)
        self.assertEqual(steps[2]["action"], "turn_off")
        self.assertEqual(steps[2]["outlet"], 2)
        self.assertIn("multi-step", detail)

    def test_alias_target_plan(self):
        alias_map = {"desk lamp": "light.desk_lamp"}
        steps, detail = parse_direct_action_plan("please turn off desk lamp", alias_map)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "turn_off")
        self.assertEqual(steps[0]["entity_alias"], "desk lamp")
        self.assertIn("deterministic", detail)


class CommandPayloadTests(unittest.TestCase):
    def test_plain_text_payload(self):
        command, source, confirm, detail = parse_command_payload("turn off plug 2")
        self.assertEqual(command, "turn off plug 2")
        self.assertEqual(source, "manual")
        self.assertFalse(confirm)
        self.assertIn("plain text", detail)

    def test_json_payload_with_source_and_confirm(self):
        command, source, confirm, detail = parse_command_payload(
            '{"command":"turn on plug 1","source":"voice","confirm":true}'
        )
        self.assertEqual(command, "turn on plug 1")
        self.assertEqual(source, "voice")
        self.assertTrue(confirm)
        self.assertIn("json", detail)

    def test_confirm_prefix(self):
        command, source, confirm, _ = parse_command_payload("confirm turn off plug 3")
        self.assertEqual(command, "turn off plug 3")
        self.assertEqual(source, "manual")
        self.assertTrue(confirm)


if __name__ == "__main__":
    unittest.main()
