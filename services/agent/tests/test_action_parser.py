import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from agent.main import parse_direct_action_command


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


if __name__ == "__main__":
    unittest.main()
