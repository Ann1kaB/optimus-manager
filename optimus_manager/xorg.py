import os
from pathlib import Path
from optimus_manager.bash import exec_bash, BashError
import optimus_manager.envs as envs
import optimus_manager.checks as checks
from .pci import get_gpus_bus_ids
from .config import load_extra_xorg_options
from .hacks.manjaro import remove_mhwd_conf
from .log_utils import get_logger

class XorgSetupError(Exception):
    pass


def configure_xorg(config, requested_gpu_mode):

    bus_ids = get_gpus_bus_ids()

    if "intel" in bus_ids:
        device_name = "intel"
    else:
        device_name = "amdgpu"

    xorg_extra = load_extra_xorg_options()

    if requested_gpu_mode == "nvidia":
        xorg_conf_text = _generate_nvidia(config, bus_ids, xorg_extra, device_name)
    elif requested_gpu_mode == "integrated":
        xorg_conf_text = _make_integrated_device_section(config, bus_ids, xorg_extra, device_name)
    elif requested_gpu_mode == "hybrid":
        xorg_conf_text = _generate_hybrid(config, bus_ids, xorg_extra, device_name)

    remove_mhwd_conf()
    _write_xorg_conf(xorg_conf_text)


def cleanup_xorg_conf():

    logger = get_logger()

    logger.info("Removing %s (if present)", envs.XORG_CONF_PATH)

    try:
        os.remove(envs.XORG_CONF_PATH)
    except FileNotFoundError:
        pass


def default_xorg_conf(config):

    logger = get_logger()

    logger.info("Defaulting to integrated mode.")

    configure_xorg(config, requested_gpu_mode="integrated")


def is_xorg_running():

    try:
        exec_bash("pidof X")
        return True
    except BashError:
        pass

    try:
        exec_bash("pidof Xorg")
        return True
    except BashError:
        pass

    return False


def is_there_a_default_xorg_conf_file():
    return os.path.isfile("/etc/X11/xorg.conf")


def is_there_a_MHWD_file():
    return os.path.isfile("/etc/X11/xorg.conf.d/90-mhwd.conf")


def do_xsetup(requested_mode, config):

    logger = get_logger()

    if requested_mode == "nvidia":
        logger.info("Running xrandr commands")

        try:
            provider = checks.get_integrated_provider()
            if config["integrated"]["driver"] == "xorg":
                exec_bash("xrandr --setprovideroutputsource \"%s\" NVIDIA-0" % provider)
            else:
                exec_bash("xrandr --setprovideroutputsource modesetting NVIDIA-0")
            exec_bash("xrandr --auto")
        except BashError as e:
            logger.error("Cannot setup PRIME : %s", str(e))

    script_path = _get_xsetup_script_path(requested_mode)

    logger.info("Running %s", script_path)

    try:
        exec_bash(script_path)
    except BashError as e:
        logger.error("ERROR : cannot run %s : %s", script_path, str(e))

    dpi_str = config["nvidia"]["dpi"]

    if dpi_str == "":
        return

    try:
        exec_bash("xrandr --dpi %s" % dpi_str)
    except BashError as e:
        raise XorgSetupError("Cannot set DPI : %s" % str(e))

def _get_xsetup_script_path(requested_mode):

    logger = get_logger()

    if requested_mode == "integrated":

        bus_ids = get_gpus_bus_ids()
        if "intel" in bus_ids:

            if Path(envs.XSETUP_SCRIPTS_PATHS["intel"]).exists():

                logger.warning(
                    "The custom xsetup script path %s is deprecated."
                    "It will still work for now, but you should move its content to %s.",
                    envs.XSETUP_SCRIPTS_PATHS["intel"],
                    envs.XSETUP_SCRIPTS_PATHS["integrated"])

                script_name = "intel"

            else:
                script_name = "integrated"

        else:
            script_name = "integrated"

    elif requested_mode == "nvidia":
        script_name = "nvidia"
    elif requested_mode == "hybrid":
        script_name = "hybrid"
    else:
        assert False

    script_path = envs.XSETUP_SCRIPTS_PATHS[script_name]

    return script_path


def _generate_nvidia(config, bus_ids, xorg_extra, device_name):

    text = _make_modules_paths_section()

    text += "Section \"ServerLayout\"\n" \
            "\tIdentifier \"layout\"\n" \
            "\tScreen 0 \"nvidia\"\n"
    text += "\tInactive \"%s\"\n" % device_name
    text += "EndSection\n\n"


    text += _make_nvidia_device_section(config, bus_ids, xorg_extra)

    text += "Section \"Screen\"\n" \
            "\tIdentifier \"nvidia\"\n" \
            "\tDevice \"nvidia\"\n" \
            "\tOption \"AllowEmptyInitialConfiguration\"\n"

    if config["nvidia"]["allow_external_gpus"] == "yes":
        text += "\tOption \"AllowExternalGpus\"\n"

    text += "EndSection\n\n"

    text += _make_integrated_device_section(config, bus_ids, xorg_extra, device_name)

    text += "Section \"Screen\"\n"
    text += "\tIdentifier \"%s\"\n" \
            "\tDevice \"%s\"\n" \
            "EndSection\n\n" % (device_name, device_name)

    text += _make_server_flags_section(config)

    return text

def _make_modules_paths_section():

    return "Section \"Files\"\n" \
           "\tModulePath \"/usr/lib/nvidia\"\n" \
           "\tModulePath \"/usr/lib32/nvidia\"\n" \
           "\tModulePath \"/usr/lib32/nvidia/xorg/modules\"\n" \
           "\tModulePath \"/usr/lib32/xorg/modules\"\n" \
           "\tModulePath \"/usr/lib64/nvidia/xorg/modules\"\n" \
           "\tModulePath \"/usr/lib64/nvidia/xorg\"\n" \
           "\tModulePath \"/usr/lib64/xorg/modules\"\n" \
           "EndSection\n\n"


def _generate_hybrid(config, bus_ids, xorg_extra, device_name):

    text = "Section \"ServerLayout\"\n" \
            "\tIdentifier \"layout\"\n" \
           "\tScreen 0 \"%s\"\n" \
           "\tInactive \"nvidia\"\n" % device_name
    if config["integrated"]["reverseprime"] != "":
        reverseprime_enabled_str = {"yes": "true", "no": "false"}[config["integrated"]["reverseprime"]]
        text += "\tOption \"AllowPRIMEDisplayOffloadSink\" \"%s\"\n" % reverseprime_enabled_str
    text += "\tOption \"AllowNVIDIAGPUScreens\"\n" \
            "EndSection\n\n"

    text += _make_integrated_device_section(config, bus_ids, xorg_extra, device_name)

    text += "Section \"Screen\"\n" \
            "\tIdentifier \"%s\"\n" \
            "\tDevice \"%s\"\n" % (device_name, device_name)

    if config["nvidia"]["allow_external_gpus"] == "yes":
        text += "\tOption \"AllowExternalGpus\"\n"

    text += "EndSection\n\n"

    text += _make_nvidia_device_section(config, bus_ids, xorg_extra)

    text += "Section \"Screen\"\n" \
            "\tIdentifier \"nvidia\"\n" \
            "\tDevice \"nvidia\"\n" \
            "EndSection\n\n"

    text += _make_server_flags_section(config)

    return text


def _make_nvidia_device_section(config, bus_ids, xorg_extra):

    options = config["nvidia"]["options"].replace(" ", "").split(",")

    text = "Section \"Device\"\n" \
           "\tIdentifier \"nvidia\"\n" \
           "\tDriver \"nvidia\"\n"
    text += "\tBusID \"%s\"\n" % bus_ids["nvidia"]
    if "overclocking" in options:
        text += "\tOption \"Coolbits\" \"28\"\n"
    if "triple_buffer" in options:
        text += "\tOption \"TripleBuffer\" \"true\"\n"
    if "nvidia" in xorg_extra.keys():
        for line in xorg_extra["nvidia"]:
            text += ("\t" + line + "\n")
    text += "EndSection\n\n"

    return text


def _make_integrated_device_section(config, bus_ids, xorg_extra, device_name):

    logger = get_logger()

    dri = int(config["integrated"]["dri"])

    text = "Section \"Device\"\n"
    text += "\tIdentifier \"%s\"\n" % device_name
    if config["integrated"]["driver"] == "xorg" and not checks.is_xorg_integrated_module_available():
        logger.warning("The Xorg module %s is not available. Defaulting to modesetting." % device_name)
        driver = "modesetting"
    elif config["integrated"]["driver"] == "xorg":
        driver = device_name
    elif config["integrated"]["driver"] != "xorg":
        driver = "modesetting"
    text += "\tDriver \"%s\"\n" % driver
    text += "\tBusID \"%s\"\n" % bus_ids[device_name]
    if config["integrated"]["accel"] != "" and "intel" in bus_ids:
        text += "\tOption \"AccelMethod\" \"%s\"\n" % config["integrated"]["accel"]
    if config["integrated"]["tearfree"] != "" and config["integrated"]["driver"] == "xorg":
        tearfree_enabled_str = {"yes": "true", "no": "false"}[config["integrated"]["tearfree"]]
        text += "\tOption \"TearFree\" \"%s\"\n" % tearfree_enabled_str
    text += "\tOption \"DRI\" \"%d\"\n" % dri
    if device_name in xorg_extra.keys():
        for line in xorg_extra[device_name]:
            text += ("\t" + line + "\n")
    text += "EndSection\n\n"

    return text


def _make_server_flags_section(config):
    if config["nvidia"]["ignore_abi"] == "yes":
        return (
            "Section \"ServerFlags\"\n"
            "\tOption \"IgnoreABI\" \"1\"\n"
            "EndSection\n\n"
        )
    return ""

def _write_xorg_conf(xorg_conf_text):

    logger = get_logger()

    filepath = Path(envs.XORG_CONF_PATH)

    try:
        os.makedirs(filepath.parent, mode=0o755, exist_ok=True)
        with open(filepath, 'w') as f:
            logger.info("Writing to %s", envs.XORG_CONF_PATH)
            f.write(xorg_conf_text)
    except IOError:
        raise XorgSetupError("Cannot write Xorg conf at %s" % str(filepath))
