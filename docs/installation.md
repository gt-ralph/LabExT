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

### Allied Vision Alvium Cameras
1. Install [Vimba X](https://www.alliedvision.com/en/products/software/vimba-x-sdk/), which brings
   the USB3/GigE transport layers and the camera driver with it.
2. Install the `vmbpy` wheel that ships inside that installation. Take the one with the platform
   tag (`win_amd64`), it bundles the VmbC libraries:
```
pip install "C:\Program Files\Allied Vision\Vimba X\api\python\vmbpy-1.1.1-py3-none-win_amd64.whl"
```
   The version in the filename tracks your Vimba X release, so check the folder rather than
   copying the command verbatim. Equivalently, add the `vmbpy` flag to the pip install
   (```pip install -e .[vmbpy]```) if you want it pulled from PyPI instead.
3. Add a `Camera` entry to your `instruments.config` naming the `CameraAlliedVisionAlvium` class.
   Give it the camera ID from the Vimba X Viewer (or from
   ```"C:\Program Files\Allied Vision\Vimba X\bin\ListCameras_VmbC.exe"```) so the right camera is
   picked when more than one is attached. See `configs/happi_setup_config.config` for an example.
4. Open the live preview with **View → Camera View** (or `Ctrl+K`, or the button in the "Couple
   Light to SiP Chip" panel) to set exposure and gain against a live image while aligning. Closing
   that window stops the stream but leaves the camera connected.
5. To keep a picture, set a folder and a filename prefix under "Save Images", tick **PNG**, **TIFF**
   or both, and press **Snap and Save**. Files are named `<prefix>_000`, `<prefix>_001`, ... and an
   existing index is never overwritten. TIFF holds the camera's raw counts at the full bit depth of
   the pixel format; PNG holds the 8-bit image exactly as displayed, percentile stretch included,
   so it is a picture of the data rather than the data itself. Ticking both writes one of each
   under the same index.
6. Once the image looks right, press **Use for CameraSnapshot** to make the current exposure, gain,
   pixel format and ROI the starting values of the `CameraSnapshot` measurement. The other
   measurement settings (number of frames, output directory, file format) are left alone.

!!! note
    Only one program can hold a camera at a time. If LabExT reports that the camera is already in
    use, close the Vimba X Viewer.

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
