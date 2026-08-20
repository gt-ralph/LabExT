# Multi-device, multi-wavelength measurement with the camera as detector

How to set up a run that sweeps wavelength over several devices with `CameraSnapshot` and an Allied
Vision Alvium, and get a relative spectral response out of it rather than a set of unrelated
pictures. The numbers quoted are measured on the happi setup on 2026-08-20; treat them as the scale
of each effect rather than as constants.

## Sweep the wavelength inside the measurement, not through the wizard

Set **wavelength stop** and **wavelength step** in `CameraSnapshot`. The measurement then steps the
laser itself and captures the full bracket set at every wavelength, which is what keeps the
alignment out of the result:

- **One to-do per device.** The stages are moved and Search for Peak is run once per to-do
  ([`StandardExperiment.run`](https://github.com/LabExT/LabExT)), so with the sweep inside the
  measurement they happen once per device, before the whole spectrum - not between wavelengths.
- **One result file per device**, with `wavelength nm` as a series beside the counts, so the
  spectrum plots live in the main window and needs no stitching afterwards.
- **One exposure ladder and one auto exposure** for the whole spectrum. That matters: whatever
  systematic an exposure carries is then identical at every wavelength and cancels in the ratio
  between two of them.

Sweeping **laser wavelength** through the experiment wizard's parameter sweep instead is the trap
this avoids. That makes one to-do per wavelength, so with *execute search for peak* enabled a
20-wavelength run over 5 devices is 100 searches, each moving the stages, and the curve records the
alignment as much as the device.

So: *auto move stages to device* on, *execute search for peak* on, and the sweep set up in the
measurement. Search for Peak then runs once per device, which is what you want.

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
| laser wavelength | first wavelength of the sweep, at the **dim** end of the band | this is where auto exposure sets the anchor, and the ladder only goes shorter from there - see below |
| **wavelength stop** | last wavelength | 0 means no sweep; either direction works, so start at the dim end and sweep up or down as needed |
| **wavelength step** | e.g. 5 nm | positive whichever way the sweep runs; every wavelength costs a settle plus every bracket and frame |
| **wavelength settle time** | 0.2 s | a frame taken while the laser is still tuning was taken at a wavelength nobody recorded |
| exposure time | peak at 60-70% at the brightest wavelength | this is the **longest** bracket; the ladder descends from it |
| gain | leave at 0 dB | gain costs dynamic range and buys nothing a longer exposure does not |
| **exposure brackets** | **3** | spans 16× with factor 4; use 4 brackets if the spectrum spans more than ~25 dB |
| **exposure bracket factor** | **4** | |
| **capture dark frame** | **on** | one dark per bracket, taken once after the sweep - the dark depends on exposure and gain, not on wavelength |
| **auto exposure** | on for multi-device, off for a single device | it runs once before the sweep, so it adapts to each device while leaving the ladder fixed within a spectrum |
| laser settle time | 0.2 s | enough on this setup - the darks came out at the expected black level with no light-leak warning |
| **fit integration ROI to spot** | **on** for multi-device | each device emits into a different part of the frame, so a hand-set box only serves one of them |
| integration ROI x/y/width/height | set explicitly, fit **off**, for a single device | a box fixed for the whole sweep cannot put its own area into the response |
| number of frames | 1 for the bulk run, 3 for a validation point | three frames per bracket is what tells you whether the coupling held |
| frame timeout | 5000 ms | must exceed the longest bracket |
| save TIFF / PNG / NPY | **all off** for the bulk run | see the data budget below |
| image output directory | e.g. `images` | a relative name lands next to the result file |
| close camera after measurement | off | keeps LabExT from re-opening the camera for every metadata read |

### Anchor the ladder at the dim end of the band

The brackets descend from the exposure at the first wavelength, so that exposure decides how far
down the spectrum the measurement can still see - the short brackets are there to catch the bright
end, not the dim one.

Measured on this setup, 1510 to 1600 nm in 10 nm steps, three brackets, anchored at the bright end
(3208 us, 67% fill at 1510): the brackets agreed to 1-7% down to -10 dB, then to 18% at -17 dB,
and by -23.6 dB the two short brackets were reporting 63 counts, which is the sensor's noise peak
rather than any signal. About 10 dB of solid spectrum out of the 30 dB the device actually spans.

Anchored at the dim end instead, the same device needs about 58 ms at 1600 nm for 70% fill, which
with four brackets gives 58 / 14.5 / 3.6 / 0.91 ms. The dim end is then measured near 70% fill, and
the bright end - which would be 58,000 counts at the anchor - is caught unclipped by the third
bracket. That covers the whole span, for about 0.3 s per wavelength.

So: start the sweep at the dim end, and add brackets until the ladder reaches the bright end. A
clipped long bracket at the bright end is expected and costs nothing; a dim end with no exposure
long enough to see it cannot be recovered afterwards.

In the wizard, select the devices and add `CameraSnapshot`; leave its parameter sweep empty. The
capture-shape settings - brackets, bracket factor, dark frame, auto exposure, settle times, sweep
bounds, integration ROI, frame count, save flags - are marked non-sweepable and will not appear as
sweep axes.

### Data budget

A frame is 1032×1296×2 bytes = 2.7 MB. One device with 20 wavelengths × 3 brackets × 1 frame, plus
3 darks, is 63 frames = 170 MB of TIFF; five devices is 850 MB. The result file already carries
everything the analysis needs - per-frame ROI sums, dark sums, exposures, wavelengths, saturation
fractions - so turn the save flags **off** for the bulk run and keep images only for the one
validation device you check by hand.

Time per device is roughly `wavelengths × (settle + brackets × frames × exposure)` plus the search,
so a 20-point sweep with 3 brackets at 6 ms and 0.2 s settles is about 5 s of laser and camera time
per device, not counting the search.

## What to check when it finishes

One file per device, with `wavelength nm`, `bracket index` and `exposure time us` saying what each
row is:

- **`saturated pixel fraction`** - which brackets clipped. A clipped bracket 0 is normal and is what
  the shorter brackets are for. Every bracket clipped at a wavelength means that point was not
  measured, and the log names the wavelength.
- **`roi counts per second`** - the series to plot against `wavelength nm`. Agreement between the
  brackets at one wavelength is the linearity check: they should land within a few percent.
- **`integration roi`** - if the ROI is being refitted per point, watch for its width and height
  wandering. The fraction of the beam a box holds depends strongly on its size (15% at 40 px, 68%
  at 138 px on this spot), and that fraction goes straight into the response.
- **`dark frames`** - each carries its own mean and ROI sum. A "dark frame is not dark" warning
  means light is still reaching the sensor: lengthen the settle time or close up the enclosure.
- **`mean counts per second`** - do not use this one. It is whole-frame and it is not comparable
  between brackets, for the reason below.

Analysis recipe: per wavelength, take the longest bracket whose `saturated pixel fraction` is zero,
and use its `roi counts per second`. A reference sweep with the device out of the path turns the result
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

- **Sweeping `laser wavelength` through the wizard** gives one to-do per wavelength, and so one
  stage move and one search per wavelength. Use `wavelength stop` instead.
- **A long sweep cannot be stopped part-way.** The experiment checks for a stop between to-dos, and
  the sweep is inside one, so it finishes the device it is on. Shorter sweeps per device if that
  matters.
- **One dark set serves the whole sweep**, which assumes the sensor's dark level has not drifted
  over it. A sweep repeated in the other direction shows up drift as a difference between the two.
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
