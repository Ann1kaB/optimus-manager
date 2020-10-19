import os
import psutil
from pathlib import Path
import re
import dbus
import psutil
import py3nvml.py3nvml as nvml
from .bash import exec_bash, BashError
from .log_utils import get_logger
from .pci import get_gpus_bus_ids


class CheckError(Exception):
    pass


def is_ac_power_connected():

    for power_source_path in Path("/sys/class/power_supply/").iterdir():

        try:

            with open(power_source_path / "type", 'r') as f:
                if f.read().strip() != "Mains":
                    continue

            with open(power_source_path / "online", 'r') as f:
                if f.read(1) == "1":
                    return True

        except IOError:
            continue

    return False


def is_pat_available():
    try:
        exec_bash("grep -E '^flags.+ pat( |$)' /proc/cpuinfo")
        return True
    except BashError:
        return False


def get_active_renderer():

    if _is_gl_provider_nvidia():
        return "nvidia"
    else:
        if check_offloading_available():
            return "hybrid"
        else:
            return "integrated"


def is_module_available(module_name):

    try:
        exec_bash("modinfo %s" % module_name)
    except BashError:
        return False
    else:
        return True

def is_module_loaded(module_name):

    try:
        exec_bash("lsmod | grep -E \"^%s \"" % module_name)
    except BashError:
        return False
    else:
        return True

def detect_os():
    return os.path.isdir("/run/runit/service")

def _detect_init_system():

    process = psutil.Process(1)
    process_name = process.name()

    if process_name == "runit":
        if detect_os():
            init = "runit-artix"
        else:
            init = "runit-void"
    elif process_name == "systemd":
        init = "systemd"
    elif process_name == "openrc-init":
        init ="openrc"

    return init

def get_current_display_manager(init):

    if not init == "systemd":
        return _get_openrc_display_manager(init)
    else:
        pass

    if not os.path.isfile("/etc/systemd/system/display-manager.service"):
        raise CheckError("No display-manager.service file found")

    dm_service_path = os.path.realpath("/etc/systemd/system/display-manager.service")
    dm_service_filename = os.path.split(dm_service_path)[-1]
    dm_name = os.path.splitext(dm_service_filename)[0]

    return dm_name


def _get_openrc_display_manager(init):

    if not init == "openrc":
        return using_patched_GDM()
    else:
        pass

    if not os.path.isfile("/etc/init.d/xdm"):
        raise CheckError("No xdm init script fle found")

    dm_service_path = os.path.realpath("/etc/init.d/xdm")
    dm_service_filename = os.path.split(dm_service_path)[-1]
    dm_name = os.path.splitext(dm_service_filename)[0]

    return dm_name


def using_patched_GDM():

    folder_path_1 = "/etc/gdm/Prime"
    folder_path_2 = "/etc/gdm3/Prime"

    return os.path.isdir(folder_path_1) or os.path.isdir(folder_path_2)

def check_offloading_available():

    try:
        out = exec_bash("xrandr --listproviders")
    except BashError as e:
        raise CheckError("Cannot list xrandr providers : %s" % str(e))

    for line in out.splitlines():
        if re.search("^Provider [0-9]+:", line) and "name:NVIDIA-G0" in line:
            return True
    return False

def get_integrated_provider():

    try:
        provider = exec_bash("xrandr --listproviders | egrep -io \"name:.*AMD.*|name:.*Intel.*\" | sed 's/name://;s/^/\"/;s/$/\"/'")
    except BashError as e:
        raise CheckError("Cannot find Intel or AMD in xrandr providers : %s" % str(e))
    return provider

def is_xorg_integrated_module_available():

    bus_ids = get_gpus_bus_ids()

    if "intel" in bus_ids:
        return os.path.isfile("/usr/lib/xorg/modules/drivers/intel_drv.so")
    else:
        return os.path.isfile("/usr/lib/xorg/modules/drivers/amdgpu_drv.so")

def is_login_manager_active():
    return _is_service_active("display-manager")

def is_elogind_active():
    return _is_service_active("elogind")

def is_lxdm_active():
    return _is_service_active("lxdm")

def is_daemon_active(init):

    if init == "runit-artix":
        try:
            exec_bash("pgrep -a python3 | grep -o optimus_manager")
        except BashError:
            return False
        else:
            return True
    elif init == "runit-void":
        try:
            exec_bash("pgrep -a python3 | grep  -o optimus_manager")
        except BashError:
            return False
        else:
            return True
    else:
        return _is_service_active("optimus-manager")

def is_bumblebeed_service_active():
    return _is_service_active("bumblebeed")

def list_processes_on_nvidia():

    nvml.nvmlInit()

    gpu_handle = nvml.nvmlDeviceGetHandleByIndex(0)
    proc_list = nvml.nvmlDeviceGetGraphicsRunningProcesses(gpu_handle)

    result = []
    for p_nvml in proc_list:
        p_psutil = psutil.Process(p_nvml.pid)
        cmdline = p_psutil.cmdline()
        result.append({
            "pid": p_nvml.pid,
            "cmdline": cmdline[0] if len(cmdline) > 0 else ""
        })

    nvml.nvmlShutdown()

    return result


def _is_gl_provider_nvidia():

    try:
        out = exec_bash("__NV_PRIME_RENDER_OFFLOAD=0 glxinfo")
    except BashError as e:
        raise CheckError("Cannot run glxinfo : %s" % str(e))

    for line in out.splitlines():
        if "server glx vendor string: NVIDIA Corporation" in line:
            return True
    return False


def _is_elogind_present():
    return os.path.isfile("/usr/lib/libelogind.so.0")


def _is_service_active(service_name):

    if _is_elogind_present():
        return _is_service_active_bash(service_name)
    else:
        pass

    logger = get_logger()

    try:
        system_bus = dbus.SystemBus()
    except dbus.exceptions.DBusException:
        logger.warning(
            "Cannot communicate with the DBus system bus to check status of %s."
            " Is DBus running ? Falling back to bash commands", service_name)
        return _is_service_active_bash(service_name)
    else:
        return _is_service_active_dbus(system_bus, service_name)


def _is_service_active_dbus(system_bus, service_name):

    systemd = system_bus.get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1")

    try:
        unit_path = systemd.GetUnit("%s.service" % service_name, dbus_interface="org.freedesktop.systemd1.Manager")
    except dbus.exceptions.DBusException:
        return False

    optimus_manager_interface = system_bus.get_object("org.freedesktop.systemd1", unit_path)
    properties_manager = dbus.Interface(optimus_manager_interface, 'org.freedesktop.DBus.Properties')
    state = properties_manager.Get("org.freedesktop.systemd1.Unit", "SubState")

    return state == "running"


def _is_service_active_bash(service_name):

    init = _detect_init_system()

    if init == "systemd":
        try:
            exec_bash("systemctl is-active %s" % service_name)
        except BashError:
            return False
        else:
            return True

    elif init == "openrc":
        try:
            exec_bash("rc-service %s status" % service_name)
        except BashError:
            return False
        else:
            return True

    elif init in ["runit-void", "runit-artix"]:
        try:
            exec_bash("pgrep -a %s" % service_name)
        except BashError:
            return False
        else:
            return True
