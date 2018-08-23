# Copyright 2018 The RLgraph authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import unittest
import numpy as np
from rlgraph.execution.ray.apex import ApexExecutor
from rlgraph.tests.test_util import config_from_path, recursive_assert_almost_equal


class TestApexExecutor(unittest.TestCase):
    """
    Tests the ApexExecutor which provides an interface for distributing Apex-style workloads
    via Ray.
    """
    def test_learning_2x2_grid_world(self):
        """
        Tests if apex can learn a simple environment using a single worker, thus replicating
        dqn.
        """
        env_spec = dict(
            type="grid-world",
            world="2x2",
            save_mode=False
        )
        agent_config = config_from_path("configs/apex_agent_gridworld_for_2x2_grid.json")

        executor = ApexExecutor(
            environment_spec=env_spec,
            agent_config=agent_config,
        )
        # Define executor, test assembly.
        print("Successfully created executor.")

        # Executes actual workload.
        result = executor.execute_workload(workload=dict(
            num_timesteps=1000, report_interval=100, report_interval_min_seconds=1)
        )
        full_worker_stats = executor.result_by_worker()
        print("All finished episode rewards")
        print(full_worker_stats["episode_rewards"])

        print("STATES:\n{}".format(executor.local_agent.last_q_table["states"]))
        print("\n\nQ(s,a)-VALUES:\n{}".format(np.round_(executor.local_agent.last_q_table["q_values"], decimals=2)))

        # Check q-table for correct values.
        expected_q_values_per_state = {
            (1.0, 0, 0, 0): (-1, -5, 0, -1),
            (0, 1.0, 0, 0): (-1, 1, 0, 0)
        }
        for state, q_values in zip(
                executor.local_agent.last_q_table["states"], executor.local_agent.last_q_table["q_values"]
        ):
            state, q_values = tuple(state), tuple(q_values)
            assert state in expected_q_values_per_state, \
                "ERROR: state '{}' not expected in q-table as it's a terminal state!".format(state)
            recursive_assert_almost_equal(q_values, expected_q_values_per_state[state], decimals=0)

    def test_learning_cartpole(self):
        """
        Tests if apex can learn a simple environment using a single worker, thus replicating
        dqn.
        """
        env_spec = dict(
            type="openai",
            gym_env="CartPole-v0"
        )
        agent_config = config_from_path("configs/apex_agent_cartpole.json")
        executor = ApexExecutor(
            environment_spec=env_spec,
            agent_config=agent_config,
        )
        # Define executor, test assembly.
        print("Successfully created executor.")

        # Executes actual workload.
        result = executor.execute_workload(workload=dict(num_timesteps=10000, report_interval=1000,
                                                         report_interval_min_seconds=1))
        print("Finished executing workload:")
        print(result)
