# Copyright 2018 The YARL-Project, All Rights Reserved.
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

import numpy as np
from six.moves import xrange as range_
import time

from yarl.utils.util import default_dict
from yarl.execution.worker import Worker


class SingleThreadedWorker(Worker):

    def __init__(self, **kwargs):
        super(SingleThreadedWorker, self).__init__(**kwargs)

        self.logger.info("Initialized single-threaded executor with\n environment id {} and agent {}".format(
            self.environment, self.agent
        ))

    def execute_timesteps(self, num_timesteps, max_timesteps_per_episode=0, update_spec=None, deterministic=False):
        return self._execute(
            num_timesteps=num_timesteps,
            max_timesteps_per_episode=max_timesteps_per_episode,
            deterministic=deterministic,
            update_spec=update_spec
        )

    def execute_episodes(self, num_episodes, max_timesteps_per_episode=0, update_spec=None, deterministic=False):
        return self._execute(
            num_episodes=num_episodes,
            max_timesteps_per_episode = max_timesteps_per_episode,
            deterministic=deterministic,
            update_spec=update_spec
        )

    def _execute(
        self,
        num_timesteps=None,
        num_episodes=None,
        max_timesteps_per_episode=None,
        deterministic=False,
        update_spec=None
    ):
        """
        Actual implementation underlying `execute_timesteps` and `execute_episodes`.

        Args:
            num_timesteps (Optional[int]): The maximum number of timesteps to run. At least one of `num_timesteps` or
                `num_episodes` must be provided.
            num_episodes (Optional[int]): The maximum number of episodes to run. At least one of `num_timesteps` or
                `num_episodes` must be provided.
            deterministic (bool): Whether to execute actions deterministically or not.
                Default: False.
            max_timesteps_per_episode (Optional[int]): Can be used to limit the number of timesteps per episode.
                Use None or 0 for no limit. Default: None.
            update_spec (Optional[dict]): Update parameters. If None, the worker only performs rollouts.
                Matches the structure of an Agent's update_spec dict and will be "defaulted" by that dict.
                See `input_parsing/parse_update_spec.py` for more details.
        Returns:
            dict: Execution statistics.
        """
        assert num_timesteps is not None or num_episodes is not None, "ERROR: One of `num_timesteps` or `num_episodes` " \
                                                                      "must be provided!"
        # Are we updating or just acting/observing?
        update_spec = default_dict(update_spec, self.agent.update_spec)
        self.set_update_schedule(update_spec)

        num_timesteps = num_timesteps or 0
        num_episodes = num_episodes or 0
        max_timesteps_per_episode = max_timesteps_per_episode or 0

        # Stats.
        timesteps_executed = 0
        episodes_executed = 0
        env_frames = 0
        episode_rewards = list()
        episode_durations = list()
        episode_steps = list()
        start = time.monotonic()

        # Only run everything for at most num_timesteps (if defined).
        while not (0 < num_timesteps <= timesteps_executed):
            # The reward accumulated over one episode.
            episode_reward = 0
            # The number of steps taken in the episode.
            episode_timestep = 0
            # Whether the episode has terminated.
            terminal = False

            # Start a new episode.
            episode_start = time.monotonic()  # wall time
            state = self.environment.reset()
            while True:
                action = self.agent.get_action(states=state, deterministic=deterministic)

                # Accumulate the reward over n env-steps (equals one action pick). n=self.repeat_actions
                reward = 0
                next_state = None
                for _ in range_(self.repeat_actions):
                    next_state, step_reward, terminal, info = self.environment.step(actions=action)
                    env_frames += 1
                    reward += step_reward
                    if terminal:
                        break

                self.agent.observe(states=state, actions=action, internals=None, rewards=reward, terminals=terminal)

                loss = self.update_if_necessary(timesteps_executed)
                #if loss is not None:
                #    self.logger.info("LOSS: {}".format(loss))

                episode_reward += reward
                timesteps_executed += 1
                episode_timestep += 1
                # Is the episode finished or do we have to terminate it prematurely because of other restrictions?
                if terminal or (0 < num_timesteps <= timesteps_executed) or \
                        (0 < max_timesteps_per_episode <= episode_timestep):
                    break

                state = next_state

            episodes_executed += 1
            episode_rewards.append(episode_reward)
            episode_durations.append(time.monotonic() - episode_start)
            episode_steps.append(episode_timestep)
            self.logger.info("Finished episode: reward={}, actions={}, duration={}s.".format(
                episode_reward, episode_timestep, episode_durations[-1]))

            if 0 < num_episodes <= episodes_executed:
                break

        total_time = (time.monotonic() - start) or 1e-10

        results = dict(
            runtime=total_time,
            # Agent act/observe throughput.
            timesteps_executed=timesteps_executed,
            ops_per_second=(timesteps_executed / total_time),
            # Env frames including action repeats.
            env_frames=env_frames,
            env_frames_per_second=(env_frames / total_time),
            episodes_executed=episodes_executed,
            episodes_per_minute=(episodes_executed/(total_time / 60)),
            mean_episode_runtime=np.mean(episode_durations),
            mean_episode_reward=np.mean(episode_rewards),
            max_episode_reward=np.max(episode_rewards),
            final_episode_reward=episode_rewards[-1]
        )

        # Total time of run.
        self.logger.info("Finished execution in {} s".format(total_time))
        # Total (RL) timesteps (actions) done (and timesteps/sec).
        self.logger.info("Time steps (actions) executed: {} ({} ops/s)".
                         format(results['timesteps_executed'], results['ops_per_second']))
        # Total env-timesteps done (including action repeats) (and env-timesteps/sec).
        self.logger.info("Env frames executed (incl. action repeats): {} ({} frames/s)".
                         format(results['env_frames'], results['env_frames_per_second']))
        # Total episodes done (and episodes/min).
        self.logger.info("Episodes finished: {} ({} episodes/min)".
                         format(results['episodes_executed'], results['episodes_per_minute']))
        self.logger.info("Mean episode runtime: {}s".format(results['mean_episode_runtime']))
        self.logger.info("Mean episode reward: {}".format(results['mean_episode_reward']))
        self.logger.info("Max. episode reward: {}".format(results['max_episode_reward']))
        self.logger.info("Final episode reward: {}".format(results['final_episode_reward']))

        return results
