import os
from pathlib import Path
from optimus_manager.bash import exec_bash, BashError
import optimus_manager.envs as envs
import optimus_manager.checks as checks
from .pci import get_gpus_bus_ids, get_available_igpu
from .config import load_extra_xorg_options
from .hacks.manjaro import remove_mhwd_conf
from .log_utils import get_logger

class XorgSetupError(Exception):
    pass


def configure_xorg(config, requested_gpu_mode):

    bus_ids = get_gpus_bus_ids()
    xorg_extra = load_extra_xorg_options()
    igpu = get_available_igpu()

    if requested_gpu_mode == "nvidia":
        xorg_conf_text = _generate_nvidia(config, bus_ids, xorg_extra, igpu)
    elif requested_gpu_mode == "igpu":
        xorg_conf_text = _generate_igpu(config, bus_ids, xorg_extra, igpu)
    elif requested_gpu_mode == "hybrid":
        xorg_conf_text = _generate_hybrid(config, bus_ids, xorg_extra, igpu)

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

    logger.info("Defaulting to igpu mode.")

    configure_xorg(config, requested_gpu_mode="igpu")


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


def do_xsetup(config, requested_mode, igpu):

    logger = get_logger()

    if requested_mode == "nvidia":
        logger.info("Running xrandr commands")

        try:
            provider = checks.get_integrated_provider()
            if config["igpu"]["driver"] == "modesetting":
                exec_bash("xrandr --setprovideroutputsource modesetting NVIDIA-0")
            else:
                exec_bash("xrandr --setprovideroutputsource %s NVIDIA-0" % provider)
            exec_bash("xrandr --auto")
        except BashError as e:
            logger.error("Cannot setup PRIME : %s", str(e))
    if requested_mode == "igpu":
        script_path = envs.XSETUP_SCRIPTS_PATHS[igpu]
    else:
        script_path = envs.XSETUP_SCRIPTS_PATHS[requested_mode]
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


def _generate_nvidia(config, bus_ids, xorg_extra, igpu):

    text = _make_modules_paths_section()

    text += "Section \"ServerLayout\"\n" \
            "\tIdentifier \"layout\"\n" \
            "\tScreen 0 \"nvidia\"\n"
    if igpu == "intel":
        text += "\tInactive \"intel\"\n"
    elif igpu == "amd":
        text += "\tInactive \"amd\"\n"
    text += "EndSection\n\n"


    text += _make_nvidia_device_section(config, bus_ids, xorg_extra)

    text += "Section \"Screen\"\n" \
            "\tIdentifier \"nvidia\"\n" \
            "\tDevice \"nvidia\"\n" \
            "\tOption \"AllowEmptyInitialConfiguration\"\n"

    if config["nvidia"]["allow_external_gpus"] == "yes":
        text += "\tOption \"AllowExternalGpus\"\n"

    text += "EndSection\n\n"

    if igpu == "intel":
        text += _make_intel_device_section(config, bus_ids, xorg_extra)
    elif igpu == "amd":
        text += _make_amd_device_section(config, bus_ids, xorg_extra)

    text += "Section \"Screen\"\n"
    if igpu == "intel":
        text += "\tIdentifier \"intel\"\n" \
                "\tDevice \"intel\"\n"
    elif igpu == "amd":
        text += "\tIdentifier \"amd\"\n" \
                "\tDevice \"amdgpu\"\n" \
                "EndSection\n\n"

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

def _generate_igpu(config, bus_ids, xorg_extra, igpu):
    if igpu == "intel":
        text = _make_intel_device_section(config, bus_ids, xorg_extra)
        return text

    elif igpu == "amd":
        text = _make_amd_device_section(config, bus_ids, xorg_extra)
        return text

def _generate_hybrid(config, bus_ids, xorg_extra, igpu):

    if igpu == "intel":
        text = "Section \"ServerLayout\"\n" \
               "\tIdentifier \"layout\"\n" \
               "\tScreen 0 \"intel\"\n" \
               "\tInactive \"nvidia\"\n"
        if config["igpu"]["reverseprime"] != "":
            reverseprime_enabled_str = {"yes": "true", "no": "false"}[config["igpu"]["reverseprime"]]
            text += "\tOption \"AllowPRIMEDisplayOffloadSink\" \"%s\"\n" % reverseprime_enabled_str
        text += "\tOption \"AllowNVIDIAGPUScreens\"\n" \
                "EndSection\n\n"

        text += _make_intel_device_section(config, bus_ids, xorg_extra)

        text += "Section \"Screen\"\n" \
                "\tIdentifier \"intel\"\n" \
                "\tDevice \"intel\"\n"

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

    elif igpu == "amd":
        text = "Section \"ServerLayout\"\n" \
               "\tIdentifier \"layout\"\n" \
               "\tScreen 0 \"amd\"\n" \
               "\tInactive \"nvidia\"\n"
        if config["igpu"]["reverseprime"] != "":
            reverseprime_enabled_str = {"yes": "true", "no": "false"}[config["igpu"]["reverseprime"]]
            text += "\tOption \"AllowPRIMEDisplayOffloadSink\" \"%s\"\n" % reverseprime_enabled_str
        text += "\tOption \"AllowNVIDIAGPUScreens\"\n" \
                "EndSection\n\n"

        text += _make_amd_device_section(config, bus_ids, xorg_extra)

        text += "Section \"Screen\"\n" \
                "\tIdentifier \"amd\"\n" \
                "\tDevice \"amd\"\n"

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

def _generate_hybrid_amd(config, bus_ids, xorg_extra):

    text = _make_modules_paths_section()

    text += "Section \"ServerLayout\"\n" \
           "\tIdentifier \"layout\"\n" \
           "\tScreen 0 \"amd\"\n" \
           "\tOption \"AllowNVIDIAGPUScreens\"\n" \
           "EndSection\n\n"

    text += _make_amd_device_section(config, bus_ids, xorg_extra)

    text += "Section \"Screen\"\n" \
            "\tIdentifier \"amd\"\n" \
            "\tDevice \"amd\"\n" \
            "EndSection\n\n"

    text += "Section \"Device\"\n" \
            "\tIdentifier \"nvidia\"\n" \
            "\tDriver \"nvidia\"\n" \
            "EndSection\n\n"

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


def _make_intel_device_section(config, bus_ids, xorg_extra):

    logger = get_logger()

    dri = int(config["igpu"]["dri"])

    text = "Section \"Device\"\n"
    text += "\tIdentifier \"intel\"\n"
    if config["igpu"]["driver"] == "xorg" and not checks.is_xorg_intel_module_available():
        logger.warning("The Xorg module intel is not available. Defaulting to modesetting.")
        driver = "modesetting"
    elif config["igpu"]["driver"] == "xorg":
        driver = "intel"
    elif config["igpu"]["driver"] != "xorg":
        driver = "modesetting"
    text += "\tDriver \"%s\"\n" % driver
    text += "\tBusID \"%s\"\n" % bus_ids["intel"]
    if config["igpu"]["accel"] != "":
        text += "\tOption \"AccelMethod\" \"%s\"\n" % config["igpu"]["accel"]
    if config["igpu"]["tearfree"] != "" and config["igpu"]["driver"] == "xorg":
        tearfree_enabled_str = {"yes": "true", "no": "false"}[config["igpu"]["tearfree"]]
        text += "\tOption \"TearFree\" \"%s\"\n" % tearfree_enabled_str
    text += "\tOption \"DRI\" \"%d\"\n" % dri
    if "intel" in xorg_extra.keys():
        for line in xorg_extra["intel"]:
            text += ("\t" + line + "\n")
    text += "EndSection\n\n"

    return text


def _make_amd_device_section(config, bus_ids, xorg_extra):

    logger = get_logger()

    dri = int(config["igpu"]["dri"])

    text = "Section \"Device\"\n"
    text += "\tIdentifier \"amd\"\n"
    if config["igpu"]["driver"] == "xorg" and not checks.is_xorg_amdgpu_module_available():
        logger.warning("WARNING : The Xorg module amdgpu is not available. Defaulting to modesetting.")
        driver = "modesetting"
    elif config["igpu"]["driver"] == "xorg":
        driver = "amdgpu"
    elif config["igpu"]["driver"] != "xorg":
        driver = "modesetting"
    text += "\tDriver \"%s\"\n" % driver
    text += "\tBusID \"%s\"\n" % bus_ids["amd"]
    if config["igpu"]["tearfree"] != "" and config["igpu"]["driver"] == "xorg":
        tearfree_enabled_str = {"yes": "true", "no": "false"}[config["igpu"]["tearfree"]]
        text += "\tOption \"TearFree\" \"%s\"\n" % tearfree_enabled_str
    text += "\tOption \"DRI\" \"%d\"\n" % dri
    if "amd" in xorg_extra.keys():
        for line in xorg_extra["amd"]:
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
