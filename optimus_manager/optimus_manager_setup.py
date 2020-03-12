#!/usr/bin/env python3
import sys
import os
import shutil
import argparse
import optimus_manager.envs as envs
from optimus_manager.config import load_config, ConfigError
import optimus_manager.var as var
from optimus_manager.kernel_parameters import get_kernel_parameters
from optimus_manager.kernel import setup_kernel_state, KernelSetupError
from optimus_manager.xorg import configure_xorg, cleanup_xorg_conf, is_xorg_running, setup_PRIME, set_DPI, XorgSetupError
import optimus_manager.processes as processes
from optimus_manager.checks import is_daemon_active, is_elogind_active, _detect_init_system, _is_elogind_present, is_ac_power_connected
from optimus_manager.logging_utils import print_timestamp_separator


def main():

    parser = argparse.ArgumentParser(description="Display Manager setup service for the Optimus Manager tool.\n"
                                                 "https://github.com/Askannz/optimus-manager")
    parser.add_argument('--setup-boot', action='store_true')
    parser.add_argument('--setup-prime', action='store_true')
    parser.add_argument('--setup-gpu', action='store_true')

    args = parser.parse_args()

    print_timestamp_separator()
    print("Optimus Manager (Setup script) version %s" % envs.VERSION)

    if args.setup_boot:
        print("Setting up boot")

        print("Removing config copy")
        _remove_config_copy()

        print("removing last acpi_call state (if any)")
        var.remove_last_acpi_call_state()

        print("Copying user config")
        _copy_user_config()

        if is_xorg_running():
            print("Error : attempting to run the initial boot setup while a X server is already running !"
                  " Skipping initial GPU setup.")
            sys.exit(0)

        # We always clean autogenerated files before doing anything else, just in case the script crashes later on.
        print("Cleaning up leftover Xorg conf")
        cleanup_xorg_conf()

        print("Loading config")
        config = _get_config()

        print("Reading startup mode")
        startup_mode = _get_startup_mode(config)
        print("Startup mode is : %s" % startup_mode)

        print("Writing startup mode to requested GPU mode")
        _write_gpu_mode(startup_mode)

        print("Initial GPU setup")
        _setup_gpu(config, startup_mode)

    elif args.setup_prime:
        print("Setting up PRIME")

        if not _detect_init_system(init="systemd"):
            print("Checking status of optimus-manager")
        elif _detect_init_system(init="systemd"):
            print("Checking the status of optimus-manager.service")
        _abort_if_service_inactive()
        _abort_if_elogind_inactive()

        print("Loading config")
        config = _get_config()

        _setup_PRIME()
        _set_DPI(config)

    elif args.setup_gpu:
        print("Setting up the GPU")

        if not _detect_init_system(init="systemd"):
            print("Checking status of optimus-manager")
        elif _detect_init_system(init="systemd"):
            print("Checking status of optimus-manager.service")
        _abort_if_service_inactive()
        _abort_if_elogind_inactive()
        print("Cleaning up leftover Xorg conf")
        cleanup_xorg_conf()

        print("Loading config")
        config = _get_config()

        requested_mode = _get_requested_mode()
        print("Requested mode :", requested_mode)
        _setup_gpu(config, requested_mode)


def _abort_if_elogind_inactive():
    if not _detect_init_system(init="systemd"):
        if not is_elogind_active():
            print("ERROR : Elogind is either not installed or not running. Aborting.")
            sys.exit(0)


def _abort_if_service_inactive():
    if not is_daemon_active():
        print("ERROR : the optimus-manager service is not running. Aborting.")
        sys.exit(0)


def _remove_config_copy():

    if os.path.isfile(envs.USER_CONFIG_COPY_PATH):
        os.remove(envs.USER_CONFIG_COPY_PATH)


def _copy_user_config():

    try:
        temp_config_path = var.read_temp_conf_path_var()
    except var.VarError:
        config_path = envs.USER_CONFIG_PATH
    else:
        print("Using temporary configuration %s" % temp_config_path)
        var.remove_temp_conf_path_var()
        if os.path.isfile(temp_config_path):
            config_path = temp_config_path
        else:
            print("Warning : temporary config file at %s not found."
                  " Using normal config file %s instead." % (temp_config_path, envs.USER_CONFIG_PATH))
            config_path = envs.USER_CONFIG_PATH

    if os.path.isfile(config_path):
        shutil.copy(config_path, envs.USER_CONFIG_COPY_PATH)


def _get_config():

    try:
        config = load_config()
    except ConfigError as e:
        print("Error loading config file : %s" % str(e))
        sys.exit(1)

    return config


def _get_startup_mode(config):

    kernel_parameters = get_kernel_parameters()

    if kernel_parameters["startup_mode"] is None:

        print("No kernel parameter set for startup, reading from file")

        try:
            startup_mode = var.read_startup_mode()
        except var.VarError as e:
            print("Cannot read startup mode : %s.\nUsing default startup mode %s instead." % (str(e), envs.DEFAULT_STARTUP_MODE))
            startup_mode = envs.DEFAULT_STARTUP_MODE

    else:

        print("Startup kernel parameter found : %s" % kernel_parameters["startup_mode"])
        startup_mode = kernel_parameters["startup_mode"]

    if startup_mode == "ac_auto":
        print("Startup mode is ac_auto, determining mode to set")
        ac_auto_battery_option = config["optimus"]["ac_auto_battery_mode"]
        startup_mode = "nvidia" if is_ac_power_connected() else ac_auto_battery_option

    return startup_mode


def _write_gpu_mode(mode):

    try:
        print("Writing requested mode")
        var.write_requested_mode(mode)

    except var.VarError as e:
        print("Cannot write requested mode : %s" % str(e))


def _get_requested_mode():

    try:
        requested_mode = var.read_requested_mode()
    except var.VarError as e:
        print("Cannot read requested mode : %s" % str(e))
        sys.exit(1)

    return requested_mode


def _setup_gpu(config, requested_mode):

    _kill_gdm_server()

    try:
        setup_kernel_state(config, requested_mode)
        configure_xorg(config, requested_mode)

    except KernelSetupError as e:
        print("Cannot setup GPU : kernel setup error : %s" % str(e))
        sys.exit(1)

    except XorgSetupError as e:
        print("Cannot setup GPU : Xorg setup error : %s" % str(e))
        print("Cleaning up Xorg config and exiting.")
        cleanup_xorg_conf()
        sys.exit(1)


def _kill_gdm_server():

    print("Checking for GDM display servers")

    try:
        xorg_PIDs_list = processes.get_PIDs_from_process_names(["Xorg", "X"])

        for PID_value in xorg_PIDs_list:
            user = processes.get_PID_user(PID_value)
            if user == "gdm":
                print("Found a Xorg GDM process (PID %d), killing it..." % PID_value)
                processes.kill_PID(PID_value, signal="-KILL")

    except processes.ProcessesError as e:
        print("Error : cannot check for or kill the GDM display server : %s" % str(e))


def _setup_PRIME():

    try:
        setup_PRIME()
    except XorgSetupError as e:
        print("Error : cannot setup PRIME : %s" % str(e))


def _set_DPI(config):

    try:
        set_DPI(config)
    except XorgSetupError as e:
        print("Error : cannot set DPI value : %s" % str(e))


if __name__ == '__main__':
    main()
