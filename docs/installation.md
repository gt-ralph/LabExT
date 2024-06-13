# Installation Instructions

It is recommended to work with [Python virtual environments](https://docs.python.org/3.9/library/venv.html#module-venv)
or conda environments. In these installation examples, we assume that we are working on a Windows machine
and you have a working [Anaconda3](https://www.anaconda.com/products/individual/) installation available.

If you just want to use LabExT and are not interested in code development, follow the
[Installation for Usage](installation.md#installation-for-usage) instructions. If you plan to change code and do some
development for LabExT, follow the [Installation for Development](setup_dev_env.md) instructions.

After the installation of LabExT, we suggest to configure the available instruments, see
[Configuration](./settings_configuration.md).

## Installation for Usage (Generic)
We assume that you have Anaconda installed (or anything else that provides the conda environment manager). Open the 
"Anaconda Prompt" console, then the installation for usage is straight forward via conda and pip:
```
conda create -n LabExT_env python=3.9
conda activate LabExT_env
pip install LabExT-pkg
```

The installation also works into a native Python venv. In any case, we heavily recommend the usage of any type of
environment (conda, venv, ...) as LabExT installs quite a few dependencies.

## Installation for Usage (Ralph Lab)
Follow the setup steps based on which lab equipment you'd like to talk to

### Thorlabs Motors
1. Clone our forked repository for pylablib
```python
git clone https://github.com/gt-ralph/pyLabLib.git 
pip install -e
```
2. Install [Thorlabs Kinesis Driver software](https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=Motion_Control&viewtab=0)

3. After opening LabExT, update the path for the kcube drivers to: ```C:\Program Files\Thorlabs\Kinesis```

### LabJack
1. Install the [LabJack-T7 drivers](https://support.labjack.com/docs/ljm-software-installer-downloads-t4-t7-t8-digit)

2. Add the `ljm` flag to the pip install: ```pip install -e .[ljm]```

### Thorcam Control
1. Download the [Thorcam SDK](https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=ThorCam)
    - Programming Interaces: Windows SDK and Doc. for Scientific Cameras
2. Follow [these build instructons](https://github.com/Thorlabs/Camera_Examples/tree/main/Python)

### OVA Control
1. Install LabView 32-bit. IT IS IMPERATIVE YOU USE 32 bit.
2. Get a student license from Tech 
3. Add the `ova` flag to the pip install: ```pip install -e .[ova]```

### Additional tools you may find useful
1. [Everything](https://www.voidtools.com/downloads/) - File search tool
2. [KLayout](https://www.klayout.de/build.html) - GDS file viewer

## Starting LabExT

Once you installed LabExT and you wish to (re)start LabExT,
its sufficient to simply activate the conda environment again and then start LabExT.
So, open the "Anaconda Prompt" console via start menu, then type:
```
conda activate LabExT_env
``` 
Since LabExT is also a registered executable within this environment, the following is then sufficient to start it 
again:
```
LabExT
```

!!! hint
    Since LabExT is now registered as a module, you can access modules of LabExT simply by
    doing `from LabExT.Instruments.XXX import XXX` from any script executed in your Python environment.
    This can be very helpful for custom scripts which use part of LabExT (e.g. instrument driver classes, or
    Piezo Stage drivers) but are not integrated into LabExT.
