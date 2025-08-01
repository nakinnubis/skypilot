"""Main entrypoint to start a service.

This including the controller and load balancer.
"""
import argparse
import multiprocessing
import os
import pathlib
import shutil
import time
import traceback
from typing import Dict

import filelock

from sky import authentication
from sky import exceptions
from sky import sky_logging
from sky import task as task_lib
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.data import data_utils
from sky.serve import constants
from sky.serve import controller
from sky.serve import load_balancer
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants as skylet_constants
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

# Use the explicit logger name so that the logger is under the
# `sky.serve.service` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.serve.service')


def _handle_signal(service_name: str) -> None:
    """Handles the signal user sent to controller."""
    signal_file = pathlib.Path(constants.SIGNAL_FILE_PATH.format(service_name))
    user_signal = None
    if signal_file.exists():
        # Filelock is needed to prevent race condition with concurrent
        # signal writing.
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open(mode='r', encoding='utf-8') as f:
                user_signal_text = f.read().strip()
                try:
                    user_signal = serve_utils.UserSignal(user_signal_text)
                    logger.info(f'User signal received: {user_signal}')
                except ValueError:
                    logger.warning(
                        f'Unknown signal received: {user_signal}. Ignoring.')
                    user_signal = None
            # Remove the signal file, after reading it.
            signal_file.unlink()
    if user_signal is None:
        return
    assert isinstance(user_signal, serve_utils.UserSignal)
    error_type = user_signal.error_type()
    raise error_type(f'User signal received: {user_signal.value}')


def cleanup_storage(task_yaml: str) -> bool:
    """Clean up the storage for the service.

    Args:
        task_yaml: The task yaml file.

    Returns:
        True if the storage is cleaned up successfully, False otherwise.
    """
    failed = False

    try:
        task = task_lib.Task.from_yaml(task_yaml)
        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        # Need to re-construct storage object in the controller process
        # because when SkyPilot API server machine sends the yaml config to the
        # controller machine, only storage metadata is sent, not the storage
        # object itself.
        for storage in task.storage_mounts.values():
            storage.construct()
        backend.teardown_ephemeral_storage(task)
    except Exception as e:  # pylint: disable=broad-except
        logger.error('Failed to clean up storage: '
                     f'{common_utils.format_exception(e)}')
        with ux_utils.enable_traceback():
            logger.error(f'  Traceback: {traceback.format_exc()}')
        failed = True

    # Clean up any files mounted from the local disk, such as two-hop file
    # mounts.
    for file_mount in (task.file_mounts or {}).values():
        try:
            if not data_utils.is_cloud_store_url(file_mount):
                path = os.path.expanduser(file_mount)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Failed to clean up file mount {file_mount}: {e}')
            with ux_utils.enable_traceback():
                logger.error(f'  Traceback: {traceback.format_exc()}')
            failed = True

    return not failed


def _cleanup(service_name: str) -> bool:
    """Clean up all service related resources, i.e. replicas and storage."""
    failed = False
    replica_infos = serve_state.get_replica_infos(service_name)
    info2proc: Dict[replica_managers.ReplicaInfo,
                    multiprocessing.Process] = dict()
    for info in replica_infos:
        p = multiprocessing.Process(target=replica_managers.terminate_cluster,
                                    args=(info.cluster_name,))
        p.start()
        info2proc[info] = p
        # Set replica status to `SHUTTING_DOWN`
        info.status_property.sky_launch_status = (
            replica_managers.ProcessStatus.SUCCEEDED)
        info.status_property.sky_down_status = (
            replica_managers.ProcessStatus.RUNNING)
        serve_state.add_or_update_replica(service_name, info.replica_id, info)
        logger.info(f'Terminating replica {info.replica_id} ...')
    for info, p in info2proc.items():
        p.join()
        if p.exitcode == 0:
            serve_state.remove_replica(service_name, info.replica_id)
            logger.info(f'Replica {info.replica_id} terminated successfully.')
        else:
            # Set replica status to `FAILED_CLEANUP`
            info.status_property.sky_down_status = (
                replica_managers.ProcessStatus.FAILED)
            serve_state.add_or_update_replica(service_name, info.replica_id,
                                              info)
            failed = True
            logger.error(f'Replica {info.replica_id} failed to terminate.')
    versions = serve_state.get_service_versions(service_name)
    serve_state.remove_service_versions(service_name)

    def cleanup_version_storage(version: int) -> bool:
        task_yaml: str = serve_utils.generate_task_yaml_file_name(
            service_name, version)
        logger.info(f'Cleaning up storage for version {version}, '
                    f'task_yaml: {task_yaml}')
        return cleanup_storage(task_yaml)

    if not all(map(cleanup_version_storage, versions)):
        failed = True

    return failed


def _cleanup_task_run_script(job_id: int) -> None:
    """Clean up task run script.
    Please see `kubernetes-ray.yml.j2` for more details.
    """
    task_run_dir = pathlib.Path(
        skylet_constants.PERSISTENT_RUN_SCRIPT_DIR).expanduser()
    if task_run_dir.exists():
        this_task_run_script = task_run_dir / f'sky_job_{job_id}'
        if this_task_run_script.exists():
            this_task_run_script.unlink()
            logger.info(f'Task run script {this_task_run_script} removed')
        else:
            logger.warning(f'Task run script {this_task_run_script} not found')


def _start(service_name: str, tmp_task_yaml: str, job_id: int):
    """Starts the service.
    This including the controller and load balancer.
    """
    # Generate ssh key pair to avoid race condition when multiple sky.launch
    # are executed at the same time.
    authentication.get_or_generate_keys()

    # Initialize database record for the service.
    task = task_lib.Task.from_yaml(tmp_task_yaml)
    # Already checked before submit to controller.
    assert task.service is not None, task
    service_spec = task.service

    def is_recovery_mode(service_name: str) -> bool:
        """Check if service exists in database to determine recovery mode.
        """
        service = serve_state.get_service_from_name(service_name)
        return service is not None

    is_recovery = is_recovery_mode(service_name)
    logger.info(f'It is a {"first" if not is_recovery else "recovery"} run')

    if is_recovery:
        version = serve_state.get_latest_version(service_name)
        if version is None:
            raise ValueError(f'No version found for service {service_name}')
    else:
        version = constants.INITIAL_VERSION
        # Add initial version information to the service state.
        serve_state.add_or_update_version(service_name, version, service_spec)

    service_dir = os.path.expanduser(
        serve_utils.generate_remote_service_dir_name(service_name))
    service_task_yaml = serve_utils.generate_task_yaml_file_name(
        service_name, version)

    if not is_recovery:
        if (len(serve_state.get_services()) >=
                serve_utils.get_num_service_threshold()):
            cleanup_storage(tmp_task_yaml)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Max number of services reached.')
        success = serve_state.add_service(
            service_name,
            controller_job_id=job_id,
            policy=service_spec.autoscaling_policy_str(),
            requested_resources_str=backend_utils.get_task_resources_str(task),
            load_balancing_policy=service_spec.load_balancing_policy,
            status=serve_state.ServiceStatus.CONTROLLER_INIT,
            tls_encrypted=service_spec.tls_credential is not None)
        # Directly throw an error here. See sky/serve/api.py::up
        # for more details.
        if not success:
            cleanup_storage(tmp_task_yaml)
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Service {service_name} already exists.')

        # Create the service working directory.
        os.makedirs(service_dir, exist_ok=True)

        # Copy the tmp task yaml file to the final task yaml file.
        # This is for the service name conflict case. The _execute will
        # sync file mounts first and then realized a name conflict. We
        # don't want the new file mounts to overwrite the old one, so we
        # sync to a tmp file first and then copy it to the final name
        # if there is no name conflict.
        shutil.copy(tmp_task_yaml, service_task_yaml)

    controller_process = None
    load_balancer_process = None
    try:
        with filelock.FileLock(
                os.path.expanduser(constants.PORT_SELECTION_FILE_LOCK_PATH)):
            # Start the controller.
            controller_port = (
                common_utils.find_free_port(constants.CONTROLLER_PORT_START)
                if not is_recovery else
                serve_state.get_service_controller_port(service_name))

            def _get_controller_host():
                """Get the controller host address.
                We expose the controller to the public network when running
                inside a kubernetes cluster to allow external load balancers
                (example, for high availability load balancers) to communicate
                with the controller.
                """
                if 'KUBERNETES_SERVICE_HOST' in os.environ:
                    return '0.0.0.0'
                # Not using localhost to avoid using ipv6 address and causing
                # the following error:
                # ERROR:    [Errno 99] error while attempting to bind on address
                # ('::1', 20001, 0, 0): cannot assign requested address
                return '127.0.0.1'

            controller_host = _get_controller_host()
            controller_process = multiprocessing.Process(
                target=controller.run_controller,
                args=(service_name, service_spec, service_task_yaml,
                      controller_host, controller_port))
            controller_process.start()

            if not is_recovery:
                serve_state.set_service_controller_port(service_name,
                                                        controller_port)

            controller_addr = f'http://{controller_host}:{controller_port}'

            # Start the load balancer.
            load_balancer_port = (
                common_utils.find_free_port(constants.LOAD_BALANCER_PORT_START)
                if not is_recovery else
                serve_state.get_service_load_balancer_port(service_name))
            load_balancer_log_file = os.path.expanduser(
                serve_utils.generate_remote_load_balancer_log_file_name(
                    service_name))

            # TODO(tian): Probably we could enable multiple ports specified in
            # service spec and we could start multiple load balancers.
            # After that, we will have a mapping from replica port to endpoint.
            load_balancer_process = multiprocessing.Process(
                target=ux_utils.RedirectOutputForProcess(
                    load_balancer.run_load_balancer,
                    load_balancer_log_file).run,
                args=(controller_addr, load_balancer_port,
                      service_spec.load_balancing_policy,
                      service_spec.tls_credential))
            load_balancer_process.start()

            if not is_recovery:
                serve_state.set_service_load_balancer_port(
                    service_name, load_balancer_port)

        while True:
            _handle_signal(service_name)
            time.sleep(1)
    except exceptions.ServeUserTerminatedError:
        serve_state.set_service_status_and_active_versions(
            service_name, serve_state.ServiceStatus.SHUTTING_DOWN)
    finally:
        # Kill load balancer process first since it will raise errors if failed
        # to connect to the controller. Then the controller process.
        process_to_kill = [
            proc for proc in [load_balancer_process, controller_process]
            if proc is not None
        ]
        subprocess_utils.kill_children_processes(
            parent_pids=[process.pid for process in process_to_kill],
            force=True)
        for process in process_to_kill:
            process.join()

        failed = _cleanup(service_name)
        if failed:
            serve_state.set_service_status_and_active_versions(
                service_name, serve_state.ServiceStatus.FAILED_CLEANUP)
            logger.error(f'Service {service_name} failed to clean up.')
        else:
            shutil.rmtree(service_dir)
            serve_state.remove_service(service_name)
            serve_state.delete_all_versions(service_name)
            logger.info(f'Service {service_name} terminated successfully.')

        _cleanup_task_run_script(job_id)


if __name__ == '__main__':
    logger.info('Starting service...')

    parser = argparse.ArgumentParser(description='Sky Serve Service')
    parser.add_argument('--service-name',
                        type=str,
                        help='Name of the service',
                        required=True)
    parser.add_argument('--task-yaml',
                        type=str,
                        help='Task YAML file',
                        required=True)
    parser.add_argument('--job-id',
                        required=True,
                        type=int,
                        help='Job id for the service job.')
    args = parser.parse_args()
    # We start process with 'spawn', because 'fork' could result in weird
    # behaviors; 'spawn' is also cross-platform.
    multiprocessing.set_start_method('spawn', force=True)
    _start(args.service_name, args.task_yaml, args.job_id)
