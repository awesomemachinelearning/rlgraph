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

from rlgraph import get_backend
from rlgraph.components.common.batch_splitter import BatchSplitter
from rlgraph.components.component import Component
from rlgraph.spaces import Dict

if get_backend() == "tf":
    import tensorflow as tf


class MultiGpuSyncOptimizer(Component):
    """
    The Multi-GPU optimizer parallelizes synchronous optimization across multiple GPUs.
    Serves as a replacement pipeline for an Agent's `update_from_external_batch` method, which
    needs to be rerouted through this Component's `calculate_update_from_external_batch` method.
    """
    def __init__(self, scope="multi-gpu-sync-optimizer", **kwargs):  #tower_optimizer_name
        """
        #Args:
        #    local_optimizer_name (str): Name of the tower optimizer names.
        """
        super(MultiGpuSyncOptimizer, self).__init__(graph_fn_num_outputs=dict(
            _graph_fn_calculate_update_from_external_batch=4  # TODO: <- This is currently hardcoded for DQN-type agents
        ), scope=scope, **kwargs)

        #self.optimizer = local_optimizer

        # Name to fetch optimizers on towers.
        #self.tower_optimizer_name = tower_optimizer_name

        ## Add local Optimizer object.
        #self.add_components(self.optimizer)

        self.towers = None
        self.dict_splitter_from_memory = None

        # Device names and variables.
        self.gpu_devices = None
        self.num_gpus = 0
        self.batch_splitter = None
        self.sub_graph_vars = None
        # Name of loss, e.g. the scope. Needed to fetch losses from subcomponents.
        self.loss_name = None

        #self.define_api_method(
        #    "load_to_device", self._graph_fn_load_to_device, flatten_ops=True, split_ops=True,
        #    add_auto_key_as_first_param=True, must_be_complete=False
        #)
        self.define_api_method("calculate_update_from_external_batch",
                               self._graph_fn_calculate_update_from_external_batch)

    def set_replicas(self, towers, loss_name, devices):
        """
        Provides the optimizer with sub-graphs, batch splitting, name of the loss to split over,
        and devices to split over.

        Args:
            towers (list): List of GPU-towers (copies of the original root-component).
            #dict_splitter (ContainerSplitter): Splitter object containing the keys needed
            #    to split an input batch into the shards for each device.
            loss_name (str): Name of loss component to fetch from sub graphs.
            devices (list): List of device names.
        """
        self.towers = towers
        self.add_components(*self.towers)
        #self.dict_splitter = dict_splitter
        self.loss_name = loss_name
        self.gpu_devices = devices
        self.num_gpus = len(devices)

        assert self.num_gpus > 1,\
            "ERROR: The MultiGPUSyncOptimizer requires as least two GPUs but only {} device ids were passed " \
            "in.".format(self.num_gpus)

        # Splits input shards of `update_from_external_batch`.
        self.batch_splitter = BatchSplitter(self.num_gpus)
        self.add_components(self.batch_splitter)

    # TODO: Solve this problem via staging areas (one per GPU).
    # TODO: Stuff coming from the batch-splitter can then be staged/unstaged (previous batch shard).
    def create_variables(self, input_spaces, action_space=None):
        super(MultiGpuSyncOptimizer, self).create_variables(input_spaces, action_space)

        # Get input space to load device fun.
        #device_input_space = dict()
        #for name, space in input_spaces.items():
        #    # TODO more elegant approach to fetch input space for these tuple spaces?
        #    if name.startswith("device_inputs"):
        #        device_input_space[name] = space

        ## Turn into container space for easy variable creation.
        #self.device_input_space = Dict.from_spec(spec=device_input_space)
        #self.sub_graph_vars = list()

        ## Create input variables for devices.
        #for device in self.gpu_devices:
        #    with tf.device(device):
        #        device_variable = self.get_variable(
        #            name="gpu_placeholder_{}".format(str(device)),
        #            trainable=False,
        #            from_space=self.device_input_space,
        #            # TODO false or True?
        #            flatten=True,
        #            add_batch_rank=True,
        #            initializer=0
        #        )
        #        self.sub_graph_vars.append(device_variable)

    def _graph_fn_calculate_update_from_external_batch(self, *inputs):
        # - Init device memory, i.e. load batch to GPU memory.
        # - Call gradient calculation on multi-gpu optimizer which splits batch
        #   and gets gradients from each sub-graph, then averages them.
        # - Apply averaged gradients to master component.
        # - Sync new weights to towers.

        # Split the incoming batch into its per-GPU shards.
        input_batches = self.call(self.batch_splitter.split_batch, *inputs)

        # Load shards to the different devices.
        # TODO: Stage each shard on its respective staging area.
        #input_batches = self.call(self._graph_fn_load_to_device, input_batches)

        # Multi gpu optimizer passes shards to the respective sub-graphs.
        #averaged_grads_and_vars, averaged_loss, averaged_loss_per_item = \
        #    self.call(self._graph_fn_calculate_gradients_and_losses, input_batches)

        all_grads_and_vars = list()
        all_loss = list()
        all_loss_per_item = list()
        all_rest = None

        assert len(input_batches) == self.num_gpus
        for i, shard_data in enumerate(input_batches):
            return_values_to_be_averaged = self.call(self.towers[i].update_from_external_batch, *shard_data)

            grads_and_vars = return_values_to_be_averaged[0]
            loss = return_values_to_be_averaged[1]
            loss_per_item = return_values_to_be_averaged[2]
            rest = return_values_to_be_averaged[3:]
            if all_rest is None:
                all_rest = [list()] * len(rest)

            all_grads_and_vars.append(grads_and_vars)
            all_loss.append(loss)
            all_loss_per_item.append(loss_per_item)
            for j, r in enumerate(rest):
                all_rest[j].append(r)

        ret = [
            # Average over the gradients per variable.
            self._average_grads_and_vars(all_grads_and_vars),
            # Simple average over all GPUs.
            tf.reduce_mean(tf.stack(all_loss, axis=0)),
            # concatenate the loss_per_item to regenerate original (un-split) batch
            tf.concat(all_loss_per_item, axis=0),
        ]

        # For the remaining return items, do like for loss-per-item (regenerate values for original, unsplit batch).
        for rest_list in all_rest:
            ret.append(tf.concat(rest_list, axis=0))

        # Return averaged gradients.
        return tuple(ret)

        # Apply averaged grads to main policy.
        #step_op = self.call(self._graph_fn_apply_gradients, averaged_grads_and_vars)

        # Get master weights.
        #weights = self.parent_component.call("get_policy_weights")
        #sync_ops = []
        #for i, shard in enumerate(self.towers):
        #    # Sync weights to shards
        #    sync_op = self.towers[i].call("set_policy_weights", weights)
        #    sync_ops.append(sync_op)

        # TODO this is the main component loss, which we don't need to calculate (would be a waste of time).
        #return tf.group([step_op] + sync_ops), averaged_loss, averaged_loss_per_item
        #return averaged_grads_and_vars, averaged_loss, averaged_loss_per_item

    #def _graph_fn_load_to_device(self, key, *device_inputs):
    #    """
    #    Loads inputs to device memories by splitting data across configured devices.

    #    Args:

    #        *DataOpTuple (DataOpTuple): Data batch.

    #    Returns:
    #        DataOpTuple: Identity op of device allocated variables.
    #    """
    #    if get_backend() == "tf":
    #        # Assign shard values to device.
    #        shard_vars = []
    #        for i, shard in enumerate(device_inputs):
    #            self.sub_graph_vars[i][key] = device_inputs[i]
    #            shard_vars.append(self.sub_graph_vars[i][key])

    #        return tuple(shard_vars)

    # TODO: OBSOLETE this, has been merged with graph_fn_calculate_update_from_external_batch
    #def OBSOLETE__graph_fn_calculate_gradients_and_losses(self, input_batches):
    #    """
    #    Calculates gradients (by variable), losses and losses_per_item by averaging them across (GPU) replicas.

    #    Args:
    #        input_batches (list): List of FlattenedDataOps containing input batch shard per item.

    #    Returns:
    #        tuple:
    #    """
    #    all_grads_and_vars = list()
    #    all_loss = list()
    #    all_loss_per_item = list()

    #    assert len(input_batches) == self.num_gpus
    #    for i, shard in enumerate(input_batches):
    #        device_inputs = self.dict_splitter.call("split", shard)

    #        ## Fetch components for this tower.
    #        ## TODO: It would be more generic to just use each tower's `update_from_external_batch`
    #        ## TODO: method with the only change being that the tower's optimizer call should not be step but
    #        ## TODO: `calculate_grads_and_vars` (and then return that for averaging here).
    #        ## TODO: This switch out of tower-optimizer API-methods could be done automatically.
    #        #tower_local_opt = self.towers[i].sub_component_by_name(self.optimizer_name)
    #        #tower_local_policy = self.towers[i].sub_component_by_name("policy")
    #        ## TODO: This may not work as these variables are shared.
    #        #variables = self.call(tower_local_policy._variables)
    #        ## Fetch by name, e.g. "dqn-loss-function", passed in by agent.
    #        #sub_graph_loss_fn = self.towers[i].sub_component_by_name(self.loss_name)
    #        #tower_loss, tower_loss_per_item = self.call(sub_graph_loss_fn.loss, *device_inputs)
    #        #all_loss.append(tower_loss)
    #        #all_loss_per_item.append(tower_loss_per_item)
    #        ## Obtain gradients for this shard.
    #        #tower_grads_and_vars = self.call(tower_local_opt.calculate_gradients, variables, tower_loss)
    #        # TODO: The following line is the replacement code for everything above.
    #        tower_grads_and_vars = self.call(self.towers[i].update_from_external_batch, *device_inputs)
    #        # END: TODO

    #        all_grads_and_vars.append(tower_grads_and_vars)

    #    # Return averaged gradients.
    #    return self._average_gradients_and_losses(all_grads_and_vars, all_loss, all_loss_per_item)

    #def _graph_fn_apply_gradients(self, grads_and_vars):
    #    """
    #    These should be the averaged gradients across devices. From the perspective of the
    #    user of this wrapped optimizer, the API does not change.
    #    """
    #    return self.optimizer._graph_fn_apply_gradients(grads_and_vars=grads_and_vars)

    @staticmethod
    def _average_grads_and_vars(grads_and_vars_all_gpus):
        """
        Utility to average gradients (per var) across towers.

        Note: ported from Ray RLLib to demonstrate the different modularization in RLGraph.

        Args:
            grads_and_vars (list): List grads_and_vars lists.

        Returns:
            list: List of grads_and_vars tuples averaged across GPUs.
        """
        gpu_grad_averages = []
        #gpu_loss_averages = []
        #gpu_loss_per_item_averages = []
        if get_backend() == "tf":
            # TODO: don't understand this block?
            for grads_and_vars in zip(*grads_and_vars_all_gpus):
                gpu_grads = []

                for grad, var in grads_and_vars:
                    if grad is not None:
                        # Add batch dimension.
                        batch_grad = tf.expand_dims(input=grad, axis=0)

                        # Add along axis for that gpu.
                        gpu_grads.append(batch_grad)

                if not gpu_grads:
                    continue

                aggregate_grads = tf.concat(axis=0, values=gpu_grads)
                mean_grad = tf.reduce_mean(input_tensor=aggregate_grads, axis=0)
                # Don't need all vars because they are shared.
                var = grads_and_vars[0][1]
                gpu_grad_averages.append((mean_grad, var))

            #gpu_loss_averages = tf.reduce_mean(tf.concat(axis=0, values=gpu_losses), axis=0)
            #gpu_loss_per_item_averages = tf.reduce_mean(tf.concat(axis=0, values=gpu_losses_per_item), axis=0)

        return gpu_grad_averages

    #@staticmethod
    #def _average_over_gpus(gpu_data):
    #    """
    #    Args:
    #        gpu_data (list): Data to average over all GPUs.

    #    Returns:
    #        list: List of data tuples averaged across GPUs.
    #    """
    #    gpu_data_averages = []
    #    if get_backend() == "tf":
    #        gpu_data_averages = tf.reduce_mean(tf.concat(axis=0, values=gpu_data), axis=0)

    #    return gpu_data_averages


    #def get_optimizer_variables(self):
    ## TODO: not sure whether multi-GPU need a local optimizer at all??
    ##    # Fetch variables both from local optimizer and sub graphs.
    ##    local_optimizer_vars = self.optimizer.get_optimizer_variables()
    ##    for sub_graph in self.towers:
    ##        sub_graph_opt = sub_graph.sub_component_by_name(self.optimizer_name)
    ##        local_optimizer_vars.extend(sub_graph_opt.get_optimizer_variables())
    ##    return local_optimizer_vars
    #    return []

