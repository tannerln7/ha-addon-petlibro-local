"""Non-authoritative AppDaemon persistence for local preferences and diagnostics."""

from __future__ import annotations

import json

import appdaemon.adapi as adapi

from state_agent import FeederTruth

class Storage:
    def __init__(self, ad: adapi.ADAPI, namespace: str, serial_number: str):
        self.ad: adapi.ADAPI = ad
        self.namespace: str = namespace
        self.food_manual_feed_grain_num_entity_id: str = 'sensor.plaf203_{}_food_manual_feed_grain_num'.format(serial_number)
        self.verified_truth_entity_id: str = 'sensor.plaf203_{}_verified_feeder_truth'.format(serial_number)

    def initialize(self):
        self.ad.set_namespace(self.namespace)

        if not self._entity_state_exists(self.food_manual_feed_grain_num_entity_id):
            self._entity_state_int_set(
                self.food_manual_feed_grain_num_entity_id,
                1,
                check_existence = False,
            )

    def terminate(self):
        self.ad.save_namespace()

    def food_manual_feed_grain_num_get(self) -> int:
        return self._entity_state_int_get(self.food_manual_feed_grain_num_entity_id)

    def food_manual_feed_grain_num_set(self, grain_num: int):
        self._entity_state_int_set(self.food_manual_feed_grain_num_entity_id, grain_num)

    def verified_truth_set(self, truth: FeederTruth):
        self._entity_state_dict_set(
            self.verified_truth_entity_id,
            truth.to_dict(),
            check_existence=self._entity_state_exists(self.verified_truth_entity_id),
        )

    def _entity_state_exists(self, name: str) -> bool:
        return self.ad.entity_exists(name, namespace=self.namespace)

    def _entity_state_int_get(self, name: str) -> int:
        return int(self.ad.get_state(name, namespace=self.namespace))

    def _entity_state_dict_set(
        self,
        name: str,
        state: dict,
        check_existence: bool = True,
    ):
        self.ad.set_state(
            name,
            state=json.dumps(state),
            namespace=self.namespace,
            check_existence=check_existence,
        )

    def _entity_state_int_set(
        self,
        name: str,
        state: int,
        check_existence: bool = True,
    ):
        self.ad.set_state(
            name,
            state=state,
            namespace=self.namespace,
            check_existence=check_existence,
        )
