# Smoke tests for SkyPilot for sky launched cluster and cluster job
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_cluster_job.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_cluster_job.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_cluster_job.py::test_job_queue
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_cluster_job.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_cluster_job.py --generic-cloud aws

import pathlib
import re
import shlex
import tempfile
import textwrap
from typing import Dict, List

import jinja2
import pytest
from smoke_tests import smoke_tests_utils
from smoke_tests.docker import docker_utils

import sky
from sky import AWS
from sky import Azure
from sky import GCP
from sky import skypilot_config
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import resources_utils


# ---------- Job Queue. ----------
@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue instead
@pytest.mark.no_scp  # SCP does not have T4 gpus. Run test_scp_job_queue instead
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus.
@pytest.mark.no_oci  # OCI does not have T4 gpus
@pytest.mark.no_hyperbolic  # Hyperbolic has low availability of T4 GPUs
@pytest.mark.resource_heavy
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
def test_job_queue(generic_cloud: str, accelerator: Dict[str, str]):
    accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'job_queue',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 -d --gpus {accelerator}:0.5 examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 -d --gpus {accelerator}:0.5 examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 -d --gpus {accelerator}:0.5 examples/job_queue/job.yaml',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
            f'sky exec {name} --gpus {accelerator}:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus {accelerator}:1 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Job Queue with Docker. ----------
@pytest.mark.no_fluidstack  # FluidStack does not support docker for now
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_paperspace  # Paperspace doesn't have T4 GPUs
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
@pytest.mark.no_hyperbolic  # Doesn't support Hyperbolic for now
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
@pytest.mark.parametrize(
    'image_id',
    [
        'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04',
        'docker:ubuntu:18.04',
        # Test latest image with python 3.11 installed by default.
        'docker:continuumio/miniconda3:24.1.2-0',
        # Test python>=3.12 where SkyPilot should automatically create a separate
        # conda env for runtime with python 3.10.
        'docker:continuumio/miniconda3:latest',
        # Axolotl image is a good example custom image that has its conda path
        # set in PATH with dockerfile and uses python>=3.12. It could test:
        #  1. we handle the env var set in dockerfile correctly
        #  2. python>=3.12 works with SkyPilot runtime.
        'docker:winglian/axolotl:main-latest'
    ])
def test_job_queue_with_docker(generic_cloud: str, image_id: str,
                               accelerator: Dict[str, str]):
    accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name() + image_id[len('docker:'):][:4]
    total_timeout_minutes = 40 if generic_cloud == 'azure' else 15
    time_to_sleep = 300 if generic_cloud == 'azure' else 200
    # Nebius support Cuda >= 12.0
    if (image_id == 'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04' and
            generic_cloud == 'nebius'):
        image_id = 'docker:nvidia/cuda:12.1.0-devel-ubuntu18.04'

    test = smoke_tests_utils.Test(
        'job_queue_with_docker',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} --image-id {image_id} examples/job_queue/cluster_docker.yaml',
            f'sky exec {name} -n {name}-1 -d --gpus {accelerator}:0.5 --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep*2} examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-2 -d --gpus {accelerator}:0.5 --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-3 -d --gpus {accelerator}:0.5 --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
            # Make sure the GPU is still visible to the container.
            f'sky exec {name} --image-id {image_id} nvidia-smi | grep -i "{accelerator}"',
            f'sky logs {name} 4 --status',
            f'sky stop -y {name}',
            # Make sure the job status preserve after stop and start the
            # cluster. This is also a test for the docker container to be
            # preserved after stop and start.
            f'sky start -y {name}',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep FAILED',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep CANCELLED',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep CANCELLED',
            f'sky exec {name} --gpus {accelerator}:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus {accelerator}:1 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            # Make sure it is still visible after an stop & start cycle.
            f'sky exec {name} --image-id {image_id} nvidia-smi | grep -i "{accelerator}"',
            f'sky logs {name} 7 --status'
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.lambda_cloud
def test_lambda_job_queue():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'lambda_job_queue',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LAMBDA_TYPE} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 --gpus A10:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 --gpus A10:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 --gpus A10:0.5 -d examples/job_queue/job.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.ibm
def test_ibm_job_queue():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'ibm_job_queue',
        [
            f'sky launch -y -c {name} --infra ibm --gpus v100',
            f'sky exec {name} -n {name}-1 --infra ibm -d examples/job_queue/job_ibm.yaml',
            f'sky exec {name} -n {name}-2 --infra ibm -d examples/job_queue/job_ibm.yaml',
            f'sky exec {name} -n {name}-3 --infra ibm -d examples/job_queue/job_ibm.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.scp
def test_scp_job_queue():
    name = smoke_tests_utils.get_cluster_name()
    num_of_gpu_launch = 1
    num_of_gpu_exec = 0.5
    test = smoke_tests_utils.Test(
        'SCP_job_queue',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.SCP_TYPE} {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_launch} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue_multinode instead
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus.
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_oci  # OCI Cloud does not have T4 gpus.
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_kubernetes  # Kubernetes not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic not support num_nodes > 1 yet
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
def test_job_queue_multinode(generic_cloud: str, accelerator: Dict[str, str]):
    accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name()
    total_timeout_minutes = 30 if generic_cloud == 'azure' else 15
    test = smoke_tests_utils.Test(
        'job_queue_multinode',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} examples/job_queue/cluster_multinode.yaml',
            f'sky exec {name} -n {name}-1 -d --gpus {accelerator}:0.5 examples/job_queue/job_multinode.yaml',
            f'sky exec {name} -n {name}-2 -d --gpus {accelerator}:0.5 examples/job_queue/job_multinode.yaml',
            f'sky launch -c {name} -n {name}-3 -d --gpus {accelerator}:0.5 examples/job_queue/job_multinode.yaml',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-1 | grep RUNNING)',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-2 | grep RUNNING)',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-3 | grep PENDING)',
            'sleep 90',
            f'sky cancel -y {name} 1',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep SETTING_UP',
            f'sky cancel -y {name} 1 2 3',
            f'sky launch -c {name} -n {name}-4 -d --gpus {accelerator} examples/job_queue/job_multinode.yaml',
            # Test the job status is correctly set to SETTING_UP, during the setup is running,
            # and the job can be cancelled during the setup.
            'sleep 5',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-4 | grep SETTING_UP)',
            f'sky cancel -y {name} 4',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-4 | grep CANCELLED)',
            f'sky exec {name} --gpus {accelerator}:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus {accelerator}:0.2 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus {accelerator}:1 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # No FluidStack VM has 8 CPUs
@pytest.mark.no_lambda_cloud  # No Lambda Cloud VM has 8 CPUs
@pytest.mark.no_vast  # Vast doesn't guarantee exactly 8 CPUs, only at least.
@pytest.mark.no_hyperbolic
def test_large_job_queue(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'large_job_queue',
        [
            f'sky launch -y -c {name} --cpus 8 --infra {generic_cloud}',
            f'for i in `seq 1 75`; do sky exec {name} -n {name}-$i -d "echo $i; sleep 100000000"; done',
            f'sky cancel -y {name} 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16',
            'sleep 90',

            # Each job takes 0.5 CPU and the default VM has 8 CPUs, so there should be 8 / 0.5 = 16 jobs running.
            # The first 16 jobs are canceled, so there should be 75 - 32 = 43 jobs PENDING.
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep -v grep | grep PENDING | wc -l | grep 43',
            # Make sure the jobs are scheduled in FIFO order
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep CANCELLED'
                for i in range(1, 17)
            ],
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep RUNNING'
                for i in range(17, 33)
            ],
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep PENDING'
                for i in range(33, 75)
            ],
            f'sky cancel -y {name} 33 35 37 39 17 18 19',
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep CANCELLED'
                for i in range(33, 40, 2)
            ],
            'sleep 10',
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep RUNNING'
                for i in [34, 36, 38]
            ],
        ],
        f'sky down -y {name}',
        timeout=25 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # No FluidStack VM has 8 CPUs
@pytest.mark.no_lambda_cloud  # No Lambda Cloud VM has 8 CPUs
@pytest.mark.no_vast  # No Vast Cloud VM has 8 CPUs
@pytest.mark.no_hyperbolic
@pytest.mark.resource_heavy
def test_fast_large_job_queue(generic_cloud: str):
    # This is to test the jobs can be scheduled quickly when there are many jobs in the queue.
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'fast_large_job_queue',
        [
            f'sky launch -y -c {name} --cpus 8 --infra {generic_cloud}',
            f'for i in `seq 1 32`; do sky exec {name} -n {name}-$i -d "echo $i"; done',
            'sleep 60',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep -v grep | grep SUCCEEDED | wc -l | grep 32',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.ibm
def test_ibm_job_queue_multinode():
    name = smoke_tests_utils.get_cluster_name()
    task_file = 'examples/job_queue/job_multinode_ibm.yaml'
    test = smoke_tests_utils.Test(
        'ibm_job_queue_multinode',
        [
            f'sky launch -y -c {name} --infra ibm --gpus v100 --num-nodes 2',
            f'sky exec {name} -n {name}-1 -d {task_file}',
            f'sky exec {name} -n {name}-2 -d {task_file}',
            f'sky launch -y -c {name} -n {name}-3 -d {task_file}',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-1 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-2 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep SETTING_UP)',
            'sleep 90',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep PENDING)',
            f'sky cancel -y {name} 1',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 1 2 3',
            f'sky launch -c {name} -n {name}-4 -d {task_file}',
            # Test the job status is correctly set to SETTING_UP, during the setup is running,
            # and the job can be cancelled during the setup.
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-4 | grep SETTING_UP)',
            f'sky cancel -y {name} 4',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-4 | grep CANCELLED)',
            f'sky exec {name} --gpus v100:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus v100:0.2 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus v100:1 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Docker with preinstalled package. ----------
@pytest.mark.no_fluidstack  # Doesn't support Fluidstack for now
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
@pytest.mark.no_hyperbolic  # Doesn't support Hyperbolic for now
# TODO(zhwu): we should fix this for kubernetes
def test_docker_preinstalled_package(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'docker_with_preinstalled_package',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --image-id docker:nginx',
            f'sky exec {name} "nginx -V"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} whoami | grep root',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Submitting multiple tasks to the same cluster. ----------
@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_oci  # OCI Cloud does not have T4 gpus
@pytest.mark.no_do  # DO does not have T4 gpus
@pytest.mark.no_nebius  # Nebius does not have T4 gpus
@pytest.mark.no_hyperbolic  # Hyperbolic has low availability of T4 GPUs
@pytest.mark.resource_heavy
def test_multi_echo(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    use_spot = True
    # EKS does not support spot instances
    if generic_cloud == 'kubernetes':
        use_spot = not smoke_tests_utils.is_eks_cluster()
    test = smoke_tests_utils.Test(
        'multi_echo',
        [
            f'python examples/multi_echo.py {name} {generic_cloud} {int(use_spot)}',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "FAILED" && exit 1 || true',
            'sleep 10',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "FAILED" && exit 1 || true',
            'sleep 30',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "FAILED" && exit 1 || true',
            'sleep 30',
            # Make sure that our job scheduler is fast enough to have at least
            # 15 RUNNING jobs in parallel.
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "RUNNING" | wc -l | awk \'{{if ($1 < 15) exit 1}}\'',
            'sleep 30',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "FAILED" && exit 1 || true',
            # This is to make sure we can finish job 32 before the test timeout.
            f'until sky logs {name} 32 --status; do echo "Waiting for job 32 to finish..."; sleep 1; done',
        ] +
        # Ensure jobs succeeded.
        [
            smoke_tests_utils.
            get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=name,
                job_id=i + 1,
                job_status=[sky.JobStatus.SUCCEEDED],
                timeout=120) for i in range(32)
        ] + [
            # ssh record will only be created on cli command like sky status on client side.
            f'sky status {name}',
            # Ensure monitor/autoscaler didn't crash on the 'assert not
            # unfulfilled' error.  If process not found, grep->ssh returns 1.
            f'ssh {name} \'ps aux | grep "[/]"monitor.py\''
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Task: 1 node training. ----------
@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_fluidstack  # Fluidstack does not have T4 gpus for now
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_scp  # SCP does not have V100 (16GB) GPUs. Run test_scp_huggingface instead.
@pytest.mark.no_hyperbolic  # Hyperbolic has low availability of T4 GPUs
@pytest.mark.resource_heavy
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
def test_huggingface(generic_cloud: str, accelerator: Dict[str, str]):
    accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} --gpus {accelerator} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.lambda_cloud
def test_lambda_huggingface(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'lambda_huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LAMBDA_TYPE} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} {smoke_tests_utils.LAMBDA_TYPE} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.scp
def test_scp_huggingface(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    num_of_gpu_launch = 1
    test = smoke_tests_utils.Test(
        'SCP_huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.SCP_TYPE} {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_launch} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} {smoke_tests_utils.SCP_TYPE} {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_launch} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Inferentia. ----------
@pytest.mark.aws
def test_inferentia():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test_inferentia',
        [
            f'sky launch -y -c {name} -t inf2.xlarge -- echo hi',
            f'sky exec {name} --gpus Inferentia2:1 echo hi',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- TPU VM. ----------
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu_vm():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'tpu_vm_app',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep STOPPED',  # Ensure the cluster is STOPPED.
            # Use retry: guard against transient errors observed for
            # just-stopped TPU VMs (#962).
            f'sky start --retry-until-up -y {name}',
            f'sky exec {name} examples/tpu/tpuvm_mnist.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take 30 mins
    )
    smoke_tests_utils.run_one_test(test)


# ---------- TPU VM Pod. ----------
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu_vm_pod():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'tpu_pod',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml --gpus tpu-v2-32 --use-spot --zone europe-west4-a',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take 30 mins
    )
    smoke_tests_utils.run_one_test(test)


# ---------- TPU Pod Slice on GKE. ----------
@pytest.mark.kubernetes
@pytest.mark.skip
def test_tpu_pod_slice_gke():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'tpu_pod_slice_gke',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml --cloud kubernetes --gpus tpu-v5-lite-podslice',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} "conda activate flax; python -c \'import jax; print(jax.devices()[0].platform);\' | grep tpu || exit 1;"',  # Ensure TPU is reachable.
            f'sky logs {name} 2 --status'
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take 30 mins
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Simple apps. ----------
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 yet
def test_multi_hostname(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    total_timeout_minutes = 25 if generic_cloud == 'azure' else 15
    test = smoke_tests_utils.Test(
        'multi_hostname',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/multi_hostname.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky logs {name} 1 | grep "My hostname:" | wc -l | grep 2',  # Ensure there are 2 hosts.
            f'sky exec {name} examples/multi_hostname.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud,
                                              total_timeout_minutes * 60),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 yet
def test_multi_node_failure(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multi_node_failure',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/failed_worker_setup.yaml || [ $? -eq 100 ]',
            f'sky logs {name} 1 --status | grep FAILED_SETUP',  # Ensure the job setup failed.
            f'sky exec {name} tests/test_yamls/failed_worker_run.yaml || [ $? -eq 100 ]',
            f'sky logs {name} 2 --status | grep FAILED',  # Ensure the job failed.
            f'sky logs {name} 2 | grep "My hostname:" | wc -l | grep 2',  # Ensure there 2 of the hosts printed their hostname.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on GCP. ----------
@pytest.mark.gcp
def test_gcp_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra gcp {smoke_tests_utils.LOW_RESOURCE_ARG} examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on AWS. ----------
@pytest.mark.aws
def test_aws_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'aws_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra aws {smoke_tests_utils.LOW_RESOURCE_ARG} examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on Azure. ----------
@pytest.mark.azure
def test_azure_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra azure {smoke_tests_utils.LOW_RESOURCE_ARG} examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on Kubernetes. ----------
@pytest.mark.kubernetes
def test_kubernetes_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'kubernetes_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra kubernetes examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 100); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 5; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on Paperspace. ----------
@pytest.mark.paperspace
def test_paperspace_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'paperspace_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra paperspace examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on RunPod. ----------
@pytest.mark.runpod
def test_runpod_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'runpod_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra runpod examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on SCP. ----------
@pytest.mark.scp
def test_scp_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'scp_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud scp {smoke_tests_utils.LOW_RESOURCE_ARG} examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Labels from task on AWS (instance_tags) ----------
@pytest.mark.aws
def test_task_labels_aws():
    if smoke_tests_utils.is_remote_server_test():
        pytest.skip('Skipping test_task_labels on remote server')
    name = smoke_tests_utils.get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='aws', region='us-east-1')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = smoke_tests_utils.Test(
            'task_labels_aws',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
                f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} {file_path}',
                # Verify with aws cli that the tags are set.
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name, 'aws ec2 describe-instances '
                    '--query "Reservations[*].Instances[*].InstanceId" '
                    '--filters "Name=instance-state-name,Values=running" '
                    f'--filters "Name=tag:skypilot-cluster-name,Values={name}*" '
                    '--filters "Name=tag:inlinelabel1,Values=inlinevalue1" '
                    '--filters "Name=tag:inlinelabel2,Values=inlinevalue2" '
                    '--region us-east-1 --output text'),
            ],
            f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Labels from task on GCP (labels) ----------
@pytest.mark.gcp
def test_task_labels_gcp():
    name = smoke_tests_utils.get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='gcp')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = smoke_tests_utils.Test(
            'task_labels_gcp',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
                f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} {file_path}',
                # Verify with gcloud cli that the tags are set
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    cmd=
                    (f'gcloud compute instances list --filter="name~\'^{name}\' AND '
                     'labels.inlinelabel1=\'inlinevalue1\' AND '
                     'labels.inlinelabel2=\'inlinevalue2\'" '
                     '--format="value(name)" | grep .')),
            ],
            f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Labels from task on Kubernetes (labels) ----------
@pytest.mark.kubernetes
def test_task_labels_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='kubernetes')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = smoke_tests_utils.Test(
            'task_labels_kubernetes',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} {file_path}',
                # Verify with kubectl that the labels are set.
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name, 'kubectl get pods '
                    '--selector inlinelabel1=inlinevalue1 '
                    '--selector inlinelabel2=inlinevalue2 '
                    '-o jsonpath=\'{.items[*].metadata.name}\' | '
                    f'grep \'^{name}\'')
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Pod Annotations on Kubernetes ----------
@pytest.mark.kubernetes
def test_add_pod_annotations_for_autodown_with_launch():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'add_pod_annotations_for_autodown_with_launch',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch Kubernetes cluster with two nodes, each being head node and worker node.
            # Autodown is set.
            f'sky launch -y -c {name} -i 10 --down --num-nodes 2 --cpus=1 --infra kubernetes',
            # Get names of the pods containing cluster name.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_1=$(kubectl get pods -o name | grep {name} | sed -n 1p) && '
                # Describe the first pod and check for annotations.
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop'
            ),
            # Get names of the pods containing cluster name.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_2=$(kubectl get pods -o name | grep {name} | sed -n 2p) && '
                # Describe the second pod and check for annotations.
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop'
            ),
        ],
        f'sky down -y {name} && '
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_add_and_remove_pod_annotations_with_autostop():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'add_and_remove_pod_annotations_with_autostop',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch Kubernetes cluster with two nodes, each being head node and worker node.
            f'sky launch -y -c {name} --num-nodes 2 --cpus=1 --infra kubernetes',
            # Set autodown on the cluster with 'autostop' command.
            f'sky autostop -y {name} -i 20 --down',
            # Get names of the pods containing cluster name.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_1=$(kubectl get pods -o name | grep {name} | sed -n 1p) && '
                # Describe the first pod and check for annotations.
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop',
            ),
            # Describe the second pod and check for annotations.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_2=$(kubectl get pods -o name | grep {name} | sed -n 2p) && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop'
            ),
            # Cancel the set autodown to remove the annotations from the pods.
            f'sky autostop -y {name} --cancel',
            # Describe the first pod and check if annotations are removed.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_1=$(kubectl get pods -o name | grep {name} | sed -n 1p) && '
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; ! echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; ! echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop',
            ),
            # Describe the second pod and check if annotations are removed.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_2=$(kubectl get pods -o name | grep {name} | sed -n 2p) && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; ! echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; ! echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop',
            ),
        ],
        f'sky down -y {name} && '
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Container logs from task on Kubernetes ----------
@pytest.mark.kubernetes
def test_container_logs_multinode_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    task_yaml = 'tests/test_yamls/test_k8s_logs.yaml'
    head_logs = (
        'all_pods=$(kubectl get pods); echo "$all_pods"; '
        f'echo "$all_pods" | grep {name} | '
        # Exclude the cloud cmd execution pod.
        'grep -v "cloud-cmd" |  '
        'grep head | '
        " awk '{print $1}' | xargs -I {} kubectl logs {}")
    worker_logs = ('all_pods=$(kubectl get pods); echo "$all_pods"; '
                   f'echo "$all_pods" | grep {name} |  grep worker | '
                   " awk '{print $1}' | xargs -I {} kubectl logs {}")
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test = smoke_tests_utils.Test(
            'container_logs_multinode_kubernetes',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                f'sky launch -y -c {name} {task_yaml} --num-nodes 2',
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{head_logs} | wc -l | grep 9',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{worker_logs} | wc -l | grep 9',
                ),
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_container_logs_two_jobs_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    task_yaml = 'tests/test_yamls/test_k8s_logs.yaml'
    pod_logs = (
        'all_pods=$(kubectl get pods); echo "$all_pods"; '
        f'echo "$all_pods" | grep {name} | '
        # Exclude the cloud cmd execution pod.
        'grep -v "cloud-cmd" |  '
        'grep head |'
        " awk '{print $1}' | xargs -I {} kubectl logs {}")
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test = smoke_tests_utils.Test(
            'test_container_logs_two_jobs_kubernetes',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                f'sky launch -y -c {name} {task_yaml}',
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | wc -l | grep 9',
                ),
                f'sky launch -y -c {name} {task_yaml}',
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | wc -l | grep 18',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 1 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 2 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 3 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 4 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 5 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 6 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 7 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 8 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 9 | wc -l | grep 2',
                ),
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_container_logs_two_simultaneous_jobs_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    task_yaml = 'tests/test_yamls/test_k8s_logs.yaml '
    pod_logs = (
        'all_pods=$(kubectl get pods); echo "$all_pods"; '
        f'echo "$all_pods" | grep {name} |  '
        # Exclude the cloud cmd execution pod.
        'grep -v "cloud-cmd" |  '
        'grep head |'
        " awk '{print $1}' | xargs -I {} kubectl logs {}")
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test = smoke_tests_utils.Test(
            'test_container_logs_two_simultaneous_jobs_kubernetes',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                f'sky launch -y -c {name}',
                f'sky exec -c {name} -d {task_yaml}',
                f'sky exec -c {name} -d {task_yaml}',
                'sleep 30',
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | wc -l | grep 18',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 1 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 2 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 3 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 4 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 5 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 6 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 7 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 8 | wc -l | grep 2',
                ),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'{pod_logs} | grep 9 | wc -l | grep 2',
                ),
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Task: n=2 nodes with setups. ----------
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_do  # DO does not have V100 gpus
@pytest.mark.no_nebius  # Nebius does not have V100 gpus
@pytest.mark.no_hyperbolic  # Hyperbolic does not have V100 gpus
@pytest.mark.skip(
    reason=
    'The resnet_distributed_tf_app is flaky, due to it failing to detect GPUs.')
def test_distributed_tf(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'resnet_distributed_tf_app',
        [
            # NOTE: running it twice will hang (sometimes?) - an app-level bug.
            f'python examples/resnet_distributed_tf_app.py {name} {generic_cloud}',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=25 * 60,  # 25 mins (it takes around ~19 mins)
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing GCP start and stop instances ----------
@pytest.mark.gcp
def test_gcp_start_stop():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp-start-stop',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/gcp_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',  # Ensure the raylet process has the correct file descriptor limit.
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=40),
            f'sky start -y {name} -i 1',
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[
                    sky.ClusterStatus.STOPPED, sky.ClusterStatus.INIT
                ],
                timeout=200),
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing Azure start and stop instances ----------
@pytest.mark.azure
def test_azure_start_stop():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure-start-stop',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/azure_start_stop.yaml',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',  # Ensure the raylet process has the correct file descriptor limit.
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky start -y {name} -i 1',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[
                    sky.ClusterStatus.STOPPED, sky.ClusterStatus.INIT
                ],
                timeout=280) +
            f'|| {{ ssh {name} "cat ~/.sky/skylet.log"; exit 1; }}',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing Autostopping ----------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_ibm  # FIX(IBM) sporadically fails, as restarted workers stay uninitialized indefinitely
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_kubernetes  # Kubernetes does not autostop yet
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 and autostop yet
def test_autostop(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # Azure takes ~ 7m15s (435s) to autostop a VM, so here we use 600 to ensure
    # the VM is stopped.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    # Launching and starting Azure clusters can take a long time too. e.g., restart
    # a stopped Azure cluster can take 7m. So we set the total timeout to 70m.
    total_timeout_minutes = 70 if generic_cloud == 'azure' else 20
    test = smoke_tests_utils.Test(
        'autostop',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} -i 1',

            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m"',

            # Ensure the cluster is not stopped early.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep UP',

            # Ensure the cluster is STOPPED.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),

            # Ensure the cluster is UP and the autostop setting is reset ('-').
            f'sky start -y {name}',
            f'sky status | grep {name} | grep -E "UP\s+-"',

            # Ensure the job succeeded.
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',

            # Test restarting the idleness timer via reset:
            f'sky autostop -y {name} -i 1',  # Idleness starts counting.
            'sleep 40',  # Almost reached the threshold.
            f'sky autostop -y {name} -i 1',  # Should restart the timer.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s" | grep {name} | grep UP',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),

            # Test restarting the idleness timer via exec:
            f'sky start -y {name}',
            f'sky status | grep {name} | grep -E "UP\s+-"',
            f'sky autostop -y {name} -i 1',  # Idleness starts counting.
            'sleep 45',  # Almost reached the threshold.
            f'sky exec {name} echo hi',  # Should restart the timer.
            'sleep 45',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    smoke_tests_utils.run_one_test(test)


def _get_cancel_task_with_cloud(name, cloud, timeout=15 * 60):
    test = smoke_tests_utils.Test(
        f'{cloud}-cancel-task',
        [
            f'sky launch -c {name} examples/resnet_app.yaml --infra {cloud} -y -d',
            # Wait the job to be scheduled and finished setup.
            f'until sky queue {name} | grep "RUNNING"; do sleep 10; done',
            # Wait the setup and initialize before the GPU process starts.
            'sleep 120',
            f'sky exec {name} "nvidia-smi | grep python"',
            f'sky logs {name} 2 --status || {{ sky logs {name} --no-follow 1 && exit 1; }}',  # Ensure the job succeeded.
            f'sky cancel -y {name} 1',
            'sleep 60',
            # check if the python job is gone.
            f'sky exec {name} "! nvidia-smi | grep python"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=timeout,
    )
    return test


# ---------- Testing `sky cancel` ----------
@pytest.mark.aws
def test_cancel_aws():
    name = smoke_tests_utils.get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'aws')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_cancel_gcp():
    name = smoke_tests_utils.get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'gcp')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_cancel_azure():
    name = smoke_tests_utils.get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'azure', timeout=30 * 60)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support V100 gpus for now
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_paperspace  # Paperspace has `gnome-shell` on nvidia-smi
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 yet
@pytest.mark.resource_heavy
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
def test_cancel_pytorch(generic_cloud: str, accelerator: Dict[str, str]):
    accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'cancel-pytorch',
        [
            f'sky launch -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} examples/resnet_distributed_torch.yaml -y -d',
            # Wait until the setup finishes.
            smoke_tests_utils.
            get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=name,
                job_id='1',
                job_status=[sky.JobStatus.RUNNING],
                timeout=150),
            # Wait the GPU process to start.
            'sleep 90',
            f'sky exec {name} --num-nodes 2 \'s=$(nvidia-smi); echo "$s"; echo "$s" | grep python || '
            # When run inside container/k8s, nvidia-smi cannot show process ids.
            # See https://github.com/NVIDIA/nvidia-docker/issues/179
            # To work around, we check if GPU utilization is greater than 0.
            f'[ $(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits) -gt 0 ]\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky cancel -y {name} 1',
            'sleep 60',
            f'sky exec {name} --num-nodes 2 \'s=$(nvidia-smi); echo "$s"; (echo "$s" | grep "No running process") || '
            # Ensure Xorg is the only process running.
            '[ $(nvidia-smi | grep -A 10 Processes | grep -A 10 === | grep -v Xorg) -eq 2 ]\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# can't use `_get_cancel_task_with_cloud()`, as command `nvidia-smi`
# requires a CUDA public image, which IBM doesn't offer
@pytest.mark.ibm
def test_cancel_ibm():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'ibm-cancel-task',
        [
            f'sky launch -y -c {name} --infra ibm examples/minimal.yaml',
            f'sky exec {name} -n {name}-1 -d  "while true; do echo \'Hello SkyPilot\'; sleep 2; done"',
            'sleep 20',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky cancel -y {name} 2',
            f'sleep 5',
            f'sky queue {name} | grep {name}-1 | grep CANCELLED',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing use-spot option ----------
@pytest.mark.no_fluidstack  # FluidStack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.no_nebius  # Nebius does not support spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.no_do
def test_use_spot(generic_cloud: str):
    """Test use-spot and sky exec."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'use-spot',
        [
            f'sky launch -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml --use-spot -y',
            f'sky logs {name} 1 --status',
            f'sky exec {name} echo hi',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_spot_instance_verification():
    """Test Azure spot instance provisioning with explicit verification.
    This test verifies that when --use-spot is specified for Azure:
    1. The cluster launches successfully
    2. The instances are actually provisioned as spot instances
    """
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure-spot-verification',
        [
            f'sky launch -c {name} --infra azure {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml --use-spot -y',
            f'sky logs {name} 1 --status', f'TARGET_VM_NAME="{name}"; '
            'VM_INFO=$(az vm list --query "[?contains(name, \'$TARGET_VM_NAME\')].{Name:name, ResourceGroup:resourceGroup}" -o tsv); '
            '[[ -z "$VM_INFO" ]] && exit 1; '
            'FULL_VM_NAME=$(echo "$VM_INFO" | awk \'{print $1}\'); '
            'RESOURCE_GROUP=$(echo "$VM_INFO" | awk \'{print $2}\'); '
            'VM_DETAILS=$(az vm list --resource-group "$RESOURCE_GROUP" '
            '--query "[?name==\'$FULL_VM_NAME\'].{Name:name, Location:location, Priority:priority}" -o table); '
            '[[ -z "$VM_DETAILS" ]] && exit 1; '
            'echo "VM Details:"; echo "$VM_DETAILS"; '
            'echo "$VM_DETAILS" | grep -qw "Spot" && exit 0 || exit 1'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_stop_gcp_spot():
    """Test GCP spot can be stopped, autostopped, restarted."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'stop_gcp_spot',
        [
            f'sky launch -c {name} --infra gcp {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot -y -- touch myfile',
            # stop should go through:
            f'sky stop {name} -y',
            f'sky start {name} -y',
            f'sky exec {name} -- ls myfile',
            f'sky logs {name} 2 --status',
            f'sky autostop {name} -i0 -y',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=90),
            f'sky start {name} -y',
            f'sky exec {name} -- ls myfile',
            f'sky logs {name} 3 --status',
            # -i option at launch should go through:
            f'sky launch -c {name} -i0 -y',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=120),
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing env ----------
def test_inline_env(generic_cloud: str):
    """Test env"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-inline-env',
        [
            f'sky launch -c {name} -y --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            'sleep 20',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env TEST_ENV2="success" "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
        smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing env file ----------
@pytest.mark.no_hyperbolic  # Hyperbolic fails to provision resources
def test_inline_env_file(generic_cloud: str):
    """Test env"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-inline-env-file',
        [
            f'sky launch -c {name} -y --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env-file examples/sample_dotenv "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
        smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing custom image ----------
@pytest.mark.aws
def test_aws_custom_image():
    """Test AWS custom image"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-aws-custom-image',
        [
            f'sky launch -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --retry-until-up -y tests/test_yamls/test_custom_image.yaml --infra aws/us-east-2 --image-id ami-062ddd90fb6f8267a',  # Nvidia image
            f'sky logs {name} 1 --status',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.resource_heavy
@pytest.mark.kubernetes
@pytest.mark.parametrize(
    'image_id',
    [
        'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04',
        'docker:ubuntu:18.04',
        # Test latest image with python 3.11 installed by default.
        'docker:continuumio/miniconda3:24.1.2-0',
        # Test python>=3.12 where SkyPilot should automatically create a separate
        # conda env for runtime with python 3.10.
        'docker:continuumio/miniconda3:latest',
    ])
def test_kubernetes_custom_image(image_id):
    """Test Kubernetes custom image"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-kubernetes-custom-image',
        [
            f'sky launch -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --retry-until-up -y tests/test_yamls/test_custom_image.yaml --infra kubernetes/none --image-id {image_id} --gpus T4:1',
            f'sky logs {name} 1 --status',
            # Try exec to run again and check if the logs are printed
            f'sky exec {name} tests/test_yamls/test_custom_image.yaml --infra kubernetes/none --image-id {image_id} --gpus T4:1 | grep "Hello 100"',
            # Make sure ssh is working with custom username
            f'ssh {name} echo hi | grep hi',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_start_stop_two_nodes():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure-start-stop-two-nodes',
        [
            f'sky launch --num-nodes=2 -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/azure_start_stop.yaml',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky start -y {name} -i 1',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[
                    sky.ClusterStatus.INIT, sky.ClusterStatus.STOPPED
                ],
                timeout=235) +
            f'|| {{ ssh {name} "cat ~/.sky/skylet.log"; exit 1; }}'
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins  (it takes around ~23 mins)
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing env for disk tier ----------
@pytest.mark.aws
def test_aws_disk_tier():

    def _get_aws_query_command(region: str, instance_id: str, field: str,
                               expected: str):
        return (f'aws ec2 describe-volumes --region {region} '
                f'--filters Name=attachment.instance-id,Values={instance_id} '
                f'--query Volumes[*].{field} | grep {expected} ; ')

    cluster_name = smoke_tests_utils.get_cluster_name()
    for disk_tier in list(resources_utils.DiskTier):
        specs = AWS._get_disk_specs(disk_tier)
        name = cluster_name + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.AWS.max_cluster_name_length())
        region = 'us-east-2'
        test = smoke_tests_utils.Test(
            'aws-disk-tier-' + disk_tier.value,
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
                f'sky launch -y -c {name} --infra aws/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
                f'--disk-tier {disk_tier.value} echo "hello sky"',
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    cmd=
                    (f'id=`aws ec2 describe-instances --region {region} --filters '
                     f'Name=tag:ray-cluster-name,Values={name_on_cloud} --query '
                     f'Reservations[].Instances[].InstanceId --output text`; ' +
                     _get_aws_query_command(region, '$id', 'VolumeType',
                                            specs['disk_tier']) +
                     ('' if specs['disk_tier']
                      == 'standard' else _get_aws_query_command(
                          region, '$id', 'Iops', specs['disk_iops'])) +
                     ('' if specs['disk_tier'] != 'gp3' else
                      _get_aws_query_command(region, '$id', 'Throughput',
                                             specs['disk_throughput'])))),
            ],
            f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
            timeout=10 * 60,  # 10 mins  (it takes around ~6 mins)
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.parametrize('instance_types',
                         [['n2-standard-2', 'n2-standard-64']])
def test_gcp_disk_tier(instance_types: List[str]):
    instance_type_low, instance_type_high = instance_types
    for disk_tier in list(resources_utils.DiskTier):
        # GCP._get_disk_type returns pd-extreme only for instance types with >= 64
        # CPUs. We must ensure the launched instance type matches what we pass to
        # GCP._get_disk_type.
        if disk_tier == resources_utils.DiskTier.BEST:
            instance_type = instance_type_high
        else:
            instance_type = instance_type_low

        disk_types = [GCP._get_disk_type(instance_type, disk_tier)]
        name = smoke_tests_utils.get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.GCP.max_cluster_name_length())
        region = 'us-central1'
        instance_type_options = ['']
        if disk_tier == resources_utils.DiskTier.BEST:
            # Ultra disk tier requires n2 instance types to have more than 64 CPUs.
            # If using default instance type, it will only enable the high disk tier.
            disk_types = [
                GCP._get_disk_type(instance_type,
                                   resources_utils.DiskTier.HIGH),
                GCP._get_disk_type(instance_type,
                                   resources_utils.DiskTier.ULTRA),
            ]
            instance_type_options = ['', f'--instance-type {instance_type}']
        for disk_type, instance_type_option in zip(disk_types,
                                                   instance_type_options):
            test = smoke_tests_utils.Test(
                'gcp-disk-tier-' + disk_tier.value,
                [
                    smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
                    f'sky launch -y -c {name} --infra gcp/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
                    f'--disk-tier {disk_tier.value} {instance_type_option} ',
                    smoke_tests_utils.run_cloud_cmd_on_cluster(
                        name,
                        cmd=(f'name=`gcloud compute instances list --filter='
                             f'"labels.ray-cluster-name:{name_on_cloud}" '
                             '--format="value(name)"`; '
                             f'gcloud compute disks list --filter="name=$name" '
                             f'--format="value(type)" | grep {disk_type}'))
                ],
                f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
                timeout=6 * 60,  # 6 mins  (it takes around ~3 mins)
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_disk_tier():
    for disk_tier in list(resources_utils.DiskTier):
        if disk_tier == resources_utils.DiskTier.HIGH or disk_tier == resources_utils.DiskTier.ULTRA:
            # Azure does not support high and ultra disk tier.
            continue
        type = Azure._get_disk_type(disk_tier)
        name = smoke_tests_utils.get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.Azure.max_cluster_name_length())
        region = 'eastus2'
        test = smoke_tests_utils.Test(
            'azure-disk-tier-' + disk_tier.value,
            [
                f'sky launch -y -c {name} --infra azure/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
                f'--disk-tier {disk_tier.value} echo "hello sky"',
                f'az resource list --tag ray-cluster-name={name_on_cloud} --query '
                f'"[?type==\'Microsoft.Compute/disks\'].sku.name" '
                f'--output tsv | grep {type}'
            ],
            f'sky down -y {name}',
            timeout=20 * 60,  # 20 mins  (it takes around ~12 mins)
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_best_tier_failover():
    type = Azure._get_disk_type(resources_utils.DiskTier.LOW)
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Azure.max_cluster_name_length())
    region = 'eastus2'
    test = smoke_tests_utils.Test(
        'azure-best-tier-failover',
        [
            f'sky launch -y -c {name} --infra azure/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--disk-tier best --instance-type Standard_D8_v5 echo "hello sky"',
            f'az resource list --tag ray-cluster-name={name_on_cloud} --query '
            f'"[?type==\'Microsoft.Compute/disks\'].sku.name" '
            f'--output tsv | grep {type}',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins  (it takes around ~12 mins)
    )
    smoke_tests_utils.run_one_test(test)


# ------ Testing Zero Quota Failover ------
@pytest.mark.aws
def test_aws_zero_quota_failover():

    name = smoke_tests_utils.get_cluster_name()
    region = smoke_tests_utils.get_aws_region_for_quota_failover()

    if not region:
        pytest.xfail(
            'Unable to test zero quota failover optimization — quotas '
            'for EC2 P3 instances were found on all AWS regions. Is this '
            'expected for your account?')
        return

    test = smoke_tests_utils.Test(
        'aws-zero-quota-failover',
        [
            f'sky launch -y -c {name} --infra aws/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus V100:8 --use-spot | grep "Found no quota"',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_zero_quota_failover():

    name = smoke_tests_utils.get_cluster_name()
    region = smoke_tests_utils.get_gcp_region_for_quota_failover()

    if not region:
        pytest.xfail(
            'Unable to test zero quota failover optimization — quotas '
            'for A100-80GB GPUs were found on all GCP regions. Is this '
            'expected for your account?')
        return

    test = smoke_tests_utils.Test(
        'gcp-zero-quota-failover',
        [
            f'sky launch -y -c {name} --infra gcp/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus A100-80GB:1 --use-spot | grep "Found no quota"',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support host controller and auto-stop
def test_long_setup_run_script(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    with tempfile.NamedTemporaryFile('w', prefix='sky_app_',
                                     suffix='.yaml') as f:
        f.write(
            textwrap.dedent(""" \
            setup: |
              echo "start long setup"
            """))
        for i in range(1024 * 200):
            f.write(f'  echo {i}\n')
        f.write('  echo "end long setup"\n')
        f.write(
            textwrap.dedent(""" \
            run: |
              echo "run"
        """))
        for i in range(1024 * 200):
            f.write(f'  echo {i}\n')
        f.write('  echo "end run"\n')
        f.flush()

        test = smoke_tests_utils.Test(
            'long-setup-run-script',
            [
                f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} {f.name}',
                f'sky exec {name} "echo hello"',
                f'sky exec {name} {f.name}',
                f'sky logs {name} --status 1',
                f'sky logs {name} --status 2',
                f'sky logs {name} --status 3',
                f'sky down {name} -y',
                f'sky jobs launch -y -n {name} --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} {f.name}',
                f'sky jobs queue | grep {name} | grep SUCCEEDED',
            ],
            f'sky down -y {name}; sky jobs cancel -n {name} -y',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Test min-gpt on Kubernetes ----------
@pytest.mark.kubernetes
@pytest.mark.resource_heavy
def test_min_gpt_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    original_yaml_path = 'examples/distributed-pytorch/train.yaml'

    with open(original_yaml_path, 'r') as f:
        content = f.read()

    # Let the train exit after 1 epoch
    modified_content = content.replace('main.py',
                                       'main.py trainer_config.max_epochs=1')

    modified_content = re.sub(r'accelerators:\s*[^\n]+', 'accelerators: T4',
                              modified_content)

    # Create a temporary YAML file with the modified content
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml') as f:
        f.write(modified_content)
        f.flush()

        test = smoke_tests_utils.Test(
            'min_gpt_kubernetes',
            [
                f'sky launch -y -c {name} --infra kubernetes {f.name}',
                f'sky logs {name} 1 --status',
            ],
            f'sky down -y {name}',
            timeout=20 * 60,
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Test GCP network tier ----------
@pytest.mark.gcp
def test_gcp_network_tier():
    """Test GCP network tier functionality for standard tier."""
    network_tier = resources_utils.NetworkTier.STANDARD
    # Use n2-standard-4 instance type for testing
    instance_type = 'n2-standard-4'
    name = smoke_tests_utils.get_cluster_name() + '-' + network_tier.value
    region = 'us-central1'

    # For standard tier, verify basic network functionality
    verification_commands = [
        smoke_tests_utils.run_cloud_cmd_on_cluster(
            name, cmd='echo "Standard network tier verification"')
    ]

    test_commands = [
        smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
        f'sky launch -y -c {name} --infra gcp/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
        f'--network-tier {network_tier.value} --instance-type {instance_type} '
        f'echo "Testing network tier {network_tier.value}"',
    ] + verification_commands

    test = smoke_tests_utils.Test(
        f'gcp-network-tier-{network_tier.value}',
        test_commands,
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        timeout=10 * 60,  # 10 mins
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_network_tier_with_gpu():
    """Test GCP network_tier=best with GPU to verify GPU Direct functionality."""
    name = smoke_tests_utils.get_cluster_name() + '-gpu-best'
    cmd = 'echo "LD_LIBRARY_PATH check for GPU workloads:" && echo $LD_LIBRARY_PATH && echo $LD_LIBRARY_PATH | grep -q "/usr/local/nvidia/lib64:/usr/local/tcpx/lib64" && echo "LD_LIBRARY_PATH contains required paths" || exit 1'
    test = smoke_tests_utils.Test(
        'gcp-network-tier-best-gpu',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            f'sky launch -y -c {name} --cloud gcp '
            f'--gpus H100:8 --network-tier best '
            f'echo "Testing network tier best with GPU"',
            # Check if LD_LIBRARY_PATH contains the required NCCL and TCPX paths for GPU workloads
            f'sky exec {name} {shlex.quote(cmd)} && sky logs {name} --status'
        ],
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        timeout=15 * 60,  # 15 mins for GPU provisioning
    )
    smoke_tests_utils.run_one_test(test)


def test_remote_server_api_login():
    if not smoke_tests_utils.is_remote_server_test():
        pytest.skip('This test is only for remote server')

    endpoint = docker_utils.get_api_server_endpoint_inside_docker()
    config_path = skypilot_config._GLOBAL_CONFIG_PATH
    backup_path = f'{config_path}.backup_for_test_remote_server_api_login'

    test = smoke_tests_utils.Test(
        'remote-server-api-login',
        [
            # Backup existing config file if it exists
            f'if [ -f {config_path} ]; then cp {config_path} {backup_path}; fi',
            # Run sky api login
            f'sky api login -e {endpoint}',
            # Echo the config file content to see what was written
            f'echo "Config file content after sky api login:" && cat {config_path}',
            # Verify the config file is updated with the endpoint
            f'grep -q "endpoint: {endpoint}" {config_path}',
            # Verify the api_server section exists
            f'grep -q "api_server:" {config_path}',
        ],
        # Restore original config file if backup exists
        f'if [ -f {backup_path} ]; then mv {backup_path} {config_path}; fi',
    )

    with pytest.MonkeyPatch().context() as m:
        m.setattr(docker_utils, 'get_api_server_endpoint_inside_docker',
                  lambda: 'http://255.255.255.255:41540')
        # Mock the environment config to return a non-existing endpoint.
        # The sky api login command should not read from environment config
        # when an explicit endpoint is provided as an argument.
        smoke_tests_utils.run_one_test(test, check_sky_status=False)
