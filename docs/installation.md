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
Follow the setup steps based on which lab equipment you'd like to talk to. Note to add multiple instruments use ```pip install -e .[flag1,flag2,flag3]```

### Thorlabs Motors
1. Clone our forked repository for pylablib
```python
git clone https://github.com/gt-ralph/pyLabLib.git
cd pyLabLib
pip install -e .
```
2. Install [Thorlabs Kinesis Driver software](https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=Motion_Control&viewtab=0)

3. After opening LabExT, update the path for the kcube drivers by going to Movement >> Configure Stage >> Load Driver for KCUBE to: ```C:\Program Files\Thorlabs\Kinesis```

### LabJack
1. Install the [LabJack-T7 drivers](https://support.labjack.com/docs/ljm-software-installer-downloads-t4-t7-t8-digit)
2. Add the `ljm` flag to the pip install: ```pip install -e .[ljm]```

### Thorcam Control
1. Download the [Thorcam SDK](https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=ThorCam)
    - Programming Interaces: Windows SDK and Doc. for Scientific Cameras
2. Follow [these build instructons](https://github.com/Thorlabs/Camera_Examples/tree/main/Python)

### OVA Control
1. Download NI Package Manager
2. Install LabView 32-bit. IT IS IMPERATIVE YOU USE 32 bit.
3. Follow [these instructions](https://gatech.service-now.com/home?id=kb_article_view&sysparm_article=KB0039617) to attach it to the institute license. Must be done this way for institute owned machines. 
4. Install the following OVA software located in the OneDrive Folder Lab>>Software>>OVA: 
    - OVA5000 v5.14.2 Installer
    - PolarizationAnalysis Desktop v5.14.2 Installer
    - PolarizationAnalysis v5.14.2 Installer
    - OVA5000 Diag Tools v5.14.2 Installer
5. Manually install the USB driver for LUNA
    - Open Device Manager
    - Go to Other Devices
    - Click on Unknown Device. If there are multiple, click the one with Port_#000... in the Location. Make sure the OVA is plugged into the computer and ON for this.
    - Update Driver...
    - Browse my computer for drivers
    - Navigate to C:\Program Files (x86)\Luna Technologies\OVA 5000 v5.14.2\USB Drivers\x64
    - Next, Install
    - To confirm you were successful, ensure ```LUNA Technologies OVA 5000``` shows up under Universal Serial Bus controllers
6. Go to OneDrive Lab>>Software>>OVA
7. Copy OVA_5000_SDK_v5.14.3 to C:
8. Open ConfigureOVA.vi
9. Ctrl+E
10. Click the configureOVA block
11. Edit the Library name or path to be ```C:\OVA_5000_SDK_v5.14.3\bin\OVA32.dll```
12. Add the `ova` flag to the pip install: ```pip install -e .[ova]```

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
