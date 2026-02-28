import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from agent.main import parse_direct_action_command
from agent.main import parse_direct_action_plan
from agent.main import parse_device_management_command
from agent.main import parse_device_management_payload
from agent.main import parse_discoverable_entity_from_topic
from agent.main import parse_command_payload
from agent.main import parse_capability_query
from agent.main import suggest_alias_from_entity_id


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

    def test_alias_spacing_variant_target_plan(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan("please turn off xiao mi fan", alias_map)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "turn_off")
        self.assertEqual(steps[0]["entity_alias"], "xiaomi fan")
        self.assertIn("deterministic", detail)

    def test_fan_speed_percentage_plan(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan("set xiaomi fan speed to 66", alias_map)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "set_percentage")
        self.assertEqual(steps[0]["entity_alias"], "xiaomi fan")
        self.assertEqual(steps[0]["percentage"], 66)
        self.assertIn("speed", detail)

    def test_fan_speed_word_plan(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan("set xiaomi fan speed high", alias_map)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "set_percentage")
        self.assertEqual(steps[0]["percentage"], 100)
        self.assertIn("preset", detail)

    def test_fan_oscillation_on_plan(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan(
            "turn on xiaomi fan oscillation",
            alias_map,
        )
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "oscillate_on")
        self.assertTrue(steps[0]["oscillating"])
        self.assertIn("oscillation", detail)

    def test_fan_oscillation_off_plan(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan(
            "turn off xiaomi fan left right swing",
            alias_map,
        )
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "oscillate_off")
        self.assertFalse(steps[0]["oscillating"])
        self.assertIn("oscillation", detail)

    def test_fan_preset_mode_plan(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan(
            "set xiaomi fan to sleeping mode",
            alias_map,
        )
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "set_preset_mode")
        self.assertEqual(steps[0]["preset_mode"], "Sleeping Mode")
        self.assertIn("preset mode", detail)

    def test_fan_speed_increase_plan(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan(
            "increase xiaomi fan speed",
            alias_map,
        )
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "increase_speed")
        self.assertIn("increase", detail)

    def test_fan_natural_soft_increase_with_single_fan(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan(
            "can you turn the fan a bit",
            alias_map,
        )
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "oscillate_brief")
        self.assertTrue(steps[0]["oscillating"])
        self.assertEqual(steps[0]["pulse_seconds"], 5)
        self.assertEqual(steps[0]["entity_alias"], "xiaomi fan")
        self.assertIn("natural", detail)

    def test_fan_natural_soft_increase_with_up_hint(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan(
            "turn the fan up a bit",
            alias_map,
        )
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "increase_speed")
        self.assertEqual(steps[0]["entity_alias"], "xiaomi fan")
        self.assertIn("natural", detail)

    def test_fan_natural_soft_decrease(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        steps, detail = parse_direct_action_plan(
            "turn the fan down a bit",
            alias_map,
        )
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "decrease_speed")
        self.assertEqual(steps[0]["entity_alias"], "xiaomi fan")
        self.assertIn("natural", detail)

    def test_capability_query_with_alias(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        is_query, targets, detail = parse_capability_query(
            "what can xiao mi fan do",
            alias_map,
        )
        self.assertTrue(is_query)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["entity_alias"], "xiaomi fan")
        self.assertIn("capabilities query", detail)

    def test_capability_query_not_action(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        is_query, targets, detail = parse_capability_query(
            "turn off xiaomi fan",
            alias_map,
        )
        self.assertFalse(is_query)
        self.assertEqual(targets, [])
        self.assertEqual(detail, "")

    def test_device_inventory_query(self):
        alias_map = {"xiaomi fan": "fan.some_fan"}
        is_query, targets, detail = parse_capability_query(
            "what devices do we have",
            alias_map,
        )
        self.assertTrue(is_query)
        self.assertEqual(targets, [])
        self.assertIn("inventory", detail)


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


class DeviceDiscoveryParserTests(unittest.TestCase):
    def test_parse_discoverable_topic(self):
        parsed = parse_discoverable_entity_from_topic(
            "home/ha/fan/dmaker_sg_1234_1c_s_2_fan/state"
        )
        self.assertEqual(parsed, ("fan", "dmaker_sg_1234_1c_s_2_fan"))

    def test_skip_non_state_topic(self):
        parsed = parse_discoverable_entity_from_topic("home/ha/fan/my_fan/availability")
        self.assertIsNone(parsed)

    def test_alias_suggestion_is_humanized(self):
        alias = suggest_alias_from_entity_id("fan.dmaker_sg_468889168_1c_s_2_fan")
        self.assertTrue("fan" in alias)
        self.assertNotIn("468889168", alias)

    def test_parse_text_approve_command(self):
        action, entity_id, alias, detail = parse_device_management_command(
            "approve device fan.dmaker_sg_1c_s_2_fan as living room fan"
        )
        self.assertEqual(action, "approve")
        self.assertEqual(entity_id, "fan.dmaker_sg_1c_s_2_fan")
        self.assertEqual(alias, "living room fan")
        self.assertIn("approval", detail)

    def test_parse_text_reject_command(self):
        action, entity_id, alias, detail = parse_device_management_command(
            "reject device fan.dmaker_sg_1c_s_2_fan"
        )
        self.assertEqual(action, "reject")
        self.assertEqual(entity_id, "fan.dmaker_sg_1c_s_2_fan")
        self.assertEqual(alias, "")
        self.assertIn("rejection", detail)

    def test_parse_json_approve_payload(self):
        action, entity_id, alias, detail = parse_device_management_payload(
            '{"device_action":"approve","entity_id":"fan.dmaker_sg_1c_s_2_fan","alias":"study fan"}'
        )
        self.assertEqual(action, "approve")
        self.assertEqual(entity_id, "fan.dmaker_sg_1c_s_2_fan")
        self.assertEqual(alias, "study fan")
        self.assertIn("JSON", detail)


if __name__ == "__main__":
    unittest.main()
