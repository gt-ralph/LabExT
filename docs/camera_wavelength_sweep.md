# Multi-device, multi-wavelength measurement with the camera as detector

How to set up a run that sweeps wavelength over several devices with `CameraSnapshot` and an Allied
Vision Alvium, and get a relative spectral response out of it rather than a set of unrelated
pictures. The numbers quoted are measured on the happi setup on 2026-08-20; treat them as the scale
of each effect rather than as constants.

## Decide first: what is the alignment doing during the sweep?

This decides whether the result means anything, and it is easy to get wrong because the default
looks harmless.

**Search for Peak runs once per to-do, not once per device.** A swept parameter creates one to-do
per value, so a 20-wavelength sweep over 5 devices is 100 to-dos - and with *execute search for
peak* enabled, 100 searches, each of which moves the stages. The coupling at every wavelength is
then whatever its own search found, so the curve mixes alignment with wavelength.

Pick the measurement you actually want:

| you want | setting |
|---|---|
| **Relative spectral response** at fixed alignment | *execute search for peak* **off** for the sweep; align each device once beforehand |
| Best achievable coupling at each wavelength | *execute search for peak* on, and do not call the result a device response |

There is currently no "align once per device, then sweep" mode. Until there is, run it per device:
align the device (Search for Peak window, or the Camera View plus the stage controls), then queue
that device's wavelength sweep with search for peak off, then move to the next device. *Pause after
each device* in the execution control settings gives you the break to do it in.

## Camera View, before the run

1. Set **pixel format** to `Mono12`. Full scale is 4095 counts and this camera clips at 4094.
2. Set the **exposure** so the peak sits around 60-70% of full scale at your *brightest* wavelength.
   6000 µs gave a peak of 2678 counts (65%) on this setup.
3. Press **Fit to spot** to set the integration ROI, then note the numbers. This is the region the
   sums are taken over, and it is shared with the peak search.
4. **Press Stop.** While the viewer streams, the camera's frame layout is fixed, so bracketing and
   auto exposure are both skipped and you get one bracket at the viewer's exposure.
5. Turn off any **ring light or illuminator** around the objective, and leave the room lights alone
   for the whole run. A ring light left on puts room light into the dark frames, which then
   subtract it off the signal as though it were sensor offset.

## CameraSnapshot settings

| parameter | value | why |
|---|---|---|
| pixel format | `Mono12` | 4095 counts of range; `Mono8` throws away four bits |
| exposure time | peak at 60-70% at the brightest wavelength | this is the **longest** bracket; the ladder descends from it |
| gain | leave at 0 dB | gain costs dynamic range and buys nothing a longer exposure does not |
| **exposure brackets** | **3** | spans 16× with factor 4; use 4 brackets if the spectrum spans more than ~25 dB |
| **exposure bracket factor** | **4** | |
| **capture dark frame** | **on** | one dark per bracket; the dark current scales with exposure, so one dark cannot serve two brackets |
| **auto exposure** | **off** | a fixed ladder is identical at every wavelength, which is what makes two points comparable |
| laser settle time | 0.2 s | enough on this setup - the darks came out at the expected black level with no light-leak warning |
| **fit integration ROI to spot** | **on** for multi-device | each device emits into a different part of the frame, so a hand-set box only serves one of them |
| integration ROI x/y/width/height | set explicitly, fit **off**, for a single device | a box fixed for the whole sweep cannot put its own area into the response |
| number of frames | 1 for the bulk run, 3 for a validation point | three frames per bracket is what tells you whether the coupling held |
| frame timeout | 5000 ms | must exceed the longest bracket |
| save TIFF / PNG / NPY | **all off** for the bulk run | see the data budget below |
| image output directory | e.g. `images` | a relative name lands next to the result file |
| close camera after measurement | off | keeps LabExT from re-opening the camera for every metadata read |

Sweep **laser wavelength** in the experiment wizard. The capture-shape settings - brackets, bracket
factor, dark frame, auto exposure, settle time, integration ROI, frame count, save flags - are
marked non-sweepable and will not appear as sweep axes.

### Data budget

A frame is 1032×1296×2 bytes = 2.7 MB, and a point with 3 brackets × 3 frames plus 3 darks is 12
frames = 32 MB of TIFF. Twenty wavelengths over five devices is 3.2 GB. The result file already
carries everything the analysis needs - per-frame ROI sums, dark sums, exposures, saturation
fractions - so turn the save flags **off** for the bulk run and keep images only for the one
validation point you check by hand.

## What to check when it finishes

Per point, in the result file:

- **`saturated pixel fraction`** - which brackets clipped. A clipped bracket 0 is normal and is what
  the shorter brackets are for. Every bracket clipped means the point was not measured, and the log
  says so.
- **`roi counts per second`** - the series to plot against wavelength. Agreement between the
  brackets of one point is the linearity check: they should land within a few percent.
- **`integration roi`** - if the ROI is being refitted per point, watch for its width and height
  wandering. The fraction of the beam a box holds depends strongly on its size (15% at 40 px, 68%
  at 138 px on this spot), and that fraction goes straight into the response.
- **`dark frames`** - each carries its own mean and ROI sum. A "dark frame is not dark" warning
  means light is still reaching the sensor: lengthen the settle time or close up the enclosure.
- **`mean counts per second`** - do not use this one. It is whole-frame and it is not comparable
  between brackets, for the reason below.

Analysis recipe: per point, take the longest bracket whose `saturated pixel fraction` is zero, and
use its `roi counts per second`. A reference sweep with the device out of the path turns the result
into the device's own response rather than the response of everything in the path, the laser's
power flatness included.

## Measured on this setup, 2026-08-20

Three brackets a factor of four apart, one device, dark frame per bracket:

| quantity | value |
|---|---|
| ROI rate across a 16× exposure ladder | 2.429e9, 2.522e9, 2.597e9 counts/s - **6% spread** |
| whole-frame rate, same frames | 2186, 3244, 6301 counts/s - **65% spread** |
| beam, summed over a region, per µs of exposure | 1177, 1249, 1227 counts/µs |
| coupling stability over 9 frames | 1.0%, 3.7%, 4.2% spread of the ROI sum within a bracket |
| dark frame | 11.82 counts of black level + 0.571 counts/ms of dark current, residuals < 0.25 counts |
| background outside the beam | 1-2 counts/px, **independent of exposure** |
| beam captured by a box | 15% at 40 px, 41% at 80 px, 68% at 138 px, 79% at 200 px |
| ROI rate spread vs box size | 5-6% for every box from 30 to 138 px, 8.5% at 200 px |

The whole-frame rate climbs as the exposure shortens because the frame outside the beam carries a
background of one to two counts per pixel which does not scale with the exposure. Over a megapixel
that is far more than the spot contributes to a frame mean, and dividing something constant by a
shorter exposure inflates it. Inside a region around the beam it is under 1% of the sum.

## Traps

- **The viewer streaming** silently costs you bracketing and auto exposure. It is logged as a
  warning; check the log if a run comes back with one bracket.
- **A saved instrument selection shadows `instruments.config`.** Removing an instrument from the
  config does not remove it from a window that once had it selected. Re-select the role and save.
  It is logged as a warning naming the class and address.
- **A dark frame is only valid at its own exposure and gain.** That is why they are taken per
  bracket and per point rather than stored, and why nothing here reuses a dark from an earlier run.
- **A frame median is not a substitute for a dark frame.** It is contaminated by the background
  above, whose sign relative to the true dark changes with coupling, so it cannot be calibrated
  out. Measured error against a real dark: 7% at good coupling, over 100% at a tenth of it.
