# Multi-device, multi-wavelength measurement with the camera as detector

How to set up a run that sweeps wavelength over several devices with `CameraSnapshot` and a camera
as the detector, and get a relative spectral response out of it rather than a set of unrelated
pictures.

Where this page gives a number it is an order of magnitude, not a constant. Every camera, chip and
alignment differs, so the sections that matter end with how to measure the value for yours.

## Sweep the wavelength inside the measurement, not through the wizard

Set **wavelength stop** and **wavelength step** in `CameraSnapshot`. The measurement then steps the
laser itself and captures the full bracket set at every wavelength, which is what keeps the
alignment out of the result:

- **One to-do per device.** The stages are moved and Search for Peak is run once per to-do, so with
  the sweep inside the measurement they happen once per device, before the whole spectrum - not
  between wavelengths.
- **One result file per device**, with `wavelength nm` as a series beside the counts, so the
  spectrum plots live in the main window and needs no stitching afterwards.
- **One exposure ladder, one integration region and one auto exposure** for the whole spectrum.
  That is what makes two wavelengths comparable: whatever systematic those carry is then identical
  at both and cancels in the ratio.

Sweeping **laser wavelength** through the experiment wizard's parameter sweep instead is the trap
this avoids. That makes one to-do per wavelength, so with *execute search for peak* enabled a
20-wavelength run over 5 devices is 100 searches, each moving the stages, and the curve records the
alignment as much as the device.

So: *auto move stages to device* on, *execute search for peak* on, and the sweep set up in the
measurement. Search for Peak then runs once per device, which is what you want.

## Before the run, in the Camera View

1. Set the **pixel format** to the deepest the camera offers. `Mono12` is the default here; the four
   bits `Mono8` gives up are the ones the dim end of a spectrum is measured with.
2. Set the **exposure** so the beam peaks around 60-70% of full scale, then read the anchor rule
   below before settling on it.
3. Press **Fit to spot**, and look at the box it draws. It should sit on the beam and exclude any
   reflections. This is the region the sums are taken over, and it is shared with the peak search.
4. **Press Stop.** While the viewer streams, the camera's frame layout is fixed, so bracketing and
   auto exposure are both skipped and you get one bracket at the viewer's exposure.
5. Turn off any **ring light or illuminator**, and leave the room lights alone for the whole run.
   Ambient light that is on for the frames and off for the darks, or vice versa, is subtracted as
   though it were sensor offset.

## CameraSnapshot settings

| parameter | value | why |
|---|---|---|
| pixel format | the deepest available | range at the dim end is what runs out first |
| laser wavelength | first wavelength, at the **dim** end of the band | this is where auto exposure sets the anchor, and the ladder only goes shorter from there |
| **wavelength stop** | last wavelength | 0 means no sweep; either direction works |
| **wavelength step** | as coarse as the features allow | every wavelength costs a settle plus every bracket and frame |
| **wavelength settle time** | long enough for the laser to arrive and its power to level | see below - too short and a frame is taken at a wavelength nobody recorded |
| exposure time | the anchor - see the anchor rule | this is the **longest** bracket; the ladder descends from it |
| gain | 0 dB | gain costs dynamic range and buys nothing a longer exposure does not |
| **exposure brackets** | enough to reach the bright end | each bracket is one *factor* further down; count them from the span of the spectrum |
| **exposure bracket factor** | 4 | two stops per bracket is a reasonable stride |
| **capture dark frame** | **on** | one dark per bracket, once for the sweep - the dark depends on exposure and gain, not on wavelength |
| **auto exposure** | on for multi-device | it runs once before the sweep, so it adapts to each device while leaving the ladder fixed within a spectrum |
| **auto exposure max** | the anchor ceiling from the rule below | this is what stops a dim device asking for an exposure the dark current owns |
| laser settle time | long enough that the darks come out dark | the log warns when they do not |
| integration ROI x/y/width/height | 0 to fit automatically, or set explicitly | see choosing the region |
| **fit integration ROI to spot** | **on** for multi-device | each device emits into a different part of the frame |
| number of frames | 1 for the bulk run, 3 for a validation point | repeats are how you tell coupling drift from spectrum |
| frame timeout | longer than the anchor | a frame that takes longer than this never arrives |
| save TIFF / PNG / NPY | **all off** for the bulk run | see the budget |
| image output directory | a relative name lands next to the result file | |
| close camera after measurement | off | keeps LabExT from re-opening the camera for every metadata read |

In the wizard, select the devices and add `CameraSnapshot`; leave its parameter sweep empty. The
capture-shape settings - brackets, bracket factor, dark frame, auto exposure, settle times, sweep
bounds, integration region, frame count, save flags - are marked non-sweepable and will not appear
as sweep axes.

## Choosing the anchor exposure

The brackets descend from the exposure at the first wavelength, so the anchor decides how far down
the spectrum the measurement can see; the short brackets catch the bright end. The temptation is to
make the anchor as long as the dim end needs. What stops you is the sensor's own dark current
inside the integration region: past some exposure the dark fills the well, and subtracting it
removes its mean but not its noise.

**To find the ceiling for your camera**, run `CameraSnapshot` once with `laser enabled` off and
several brackets, and look at the dark frames it records. Take the peak inside the integration
region, not the frame mean - a hot pixel elsewhere tells you nothing about the pixels you sum.
The longest exposure whose in-region dark peak stays around a tenth of full scale is the anchor
ceiling. Put that in **auto exposure max**.

Then count brackets: enough that the anchor divided by the factor that many times reaches an
exposure which does not clip at the brightest wavelength. A clipped long bracket at the bright end
costs nothing, because a shorter one covers it; a dim end with no exposure long enough to see it
cannot be recovered afterwards.

Some part of a wide spectrum will still be dark-current-limited rather than exposure-limited. That
is a property of the camera, not the settings, and the bracket agreement below is how you see where
it starts.

## Choosing the integration region

The reported sums are taken over this region, and the choice matters more than its size. A whole
frame is the wrong region: outside the beam every pixel contributes background that does not scale
with the exposure, and dividing that by a shorter exposure inflates it, so the whole-frame rate
climbs as the ladder descends where the region-summed rate stays flat. Measured once here, the
whole-frame rate moved by a factor of three across a ladder over which the region-summed rate held
to a few percent.

Within reason the size is not critical - anything from a fraction of the spot to a few times it
gave the same cross-bracket agreement here - but two things about it are:

- **It must exclude hot pixels and reflections.** Both grow with exposure and neither is the beam.
- **It must not change between wavelengths.** The fraction of the beam a box holds depends strongly
  on its size, and if that fraction moves during the sweep it goes straight into the response
  curve. The measurement fits once and holds, which is why.

`fit integration ROI to spot` finds the beam by the most light in a box roughly a spot across,
rather than by the brightest pixel, and subtracts a frame taken at the same exposure with the light
off so that hot pixels cancel. Both matter because a compact reflection, or a hot pixel at a long
exposure, is routinely brighter per pixel than a broad beam carrying orders of magnitude more
light. If it cannot find a beam it says so in the log and sums the whole frame, which is a result
worth discarding rather than trusting.

Set the region by hand instead when the devices all put their beam in the same place, or when the
fit picks something you can see is wrong. Check it once on a live image and leave it fixed.

## Budgets

**Data.** A frame is `width × height × 2` bytes for any 16-bit format, and a device costs
`wavelengths × brackets × frames + brackets` of them. That reaches hundreds of megabytes per device
quickly. The result file already carries everything the analysis needs - per-frame region sums, dark
sums, exposures, wavelengths, saturation fractions - so turn the save flags **off** for the bulk run
and keep images only for the one validation device you check by hand.

**Time.** Roughly `wavelengths × (settle + brackets × frames × exposure)` plus the search and the
stage move, per device.

## What to check when it finishes

One file per device, with `wavelength nm`, `bracket index` and `exposure time us` saying what each
row is. Plot against `wavelength nm` and remember there is one row per bracket: a series like
`max counts` or `exposure time us` will look like a comb, because the brackets are interleaved.

- **`roi counts per second`** - the series to plot. Agreement between the brackets at one wavelength
  is the linearity check, and it is the single best indicator in the file: a few percent means the
  ladder, the darks and the normalisation are all working, and it degrading at one end of the
  spectrum is that end reaching the noise floor.
- **`saturated pixel fraction`** - which brackets clipped. A clipped long bracket is normal and is
  what the shorter ones are for. Every bracket clipped at a wavelength means that point was not
  measured, and the log names the wavelength.
- **`integration roi`** and **`auto exposure result`** - what the run actually aimed at and settled
  on. A region far larger than the beam, or an auto exposure that did not converge, both mean the
  run started from an exposure that suited something else.
- **`dark frames`** - a "dark frame is not dark" warning means light is still reaching the sensor
  inside the region: lengthen the laser settle time or close up the enclosure.
- **`mean counts per second`** - do not use this one. It is whole-frame, for the reason above.

Analysis recipe: per wavelength, take the longest bracket whose `saturated pixel fraction` is zero,
and use its `roi counts per second`. A reference sweep with the device out of the path turns the
result into the device's own response rather than the response of everything in the path, the
laser's power flatness included.

## Characterising a new camera or setup

Worth doing once, and each one takes a single run:

| what | how | what it tells you |
|---|---|---|
| dark level versus exposure | one run, `laser enabled` off, several brackets | the anchor ceiling, and whether dark current or a fixed black level dominates |
| hot pixels | the same dark frames: look for isolated pixels far above their neighbours, and how fast they grow with exposure | which exposures the region has to be chosen carefully at |
| linearity | one wavelength, three brackets, `number of frames` 3 | cross-bracket agreement is the whole chain's accuracy; frame-to-frame spread within a bracket is coupling stability |
| region size sensitivity | re-sum saved frames over boxes of several sizes | how much the region size matters before it starts costing signal or collecting background |
| usable dynamic range | one full sweep | where the bracket agreement degrades is where the spectrum stops being a measurement |

## Traps

- **Sweeping `laser wavelength` through the wizard** gives one to-do per wavelength, and so one
  stage move and one search per wavelength. Use `wavelength stop` instead.
- **A long sweep cannot be stopped part-way.** The experiment checks for a stop between to-dos, and
  the sweep is inside one, so it finishes the device it is on.
- **One dark set serves the whole sweep**, which assumes the dark level has not drifted over it. A
  sweep repeated in the other direction shows up drift as a difference between the two.
- **The viewer streaming** silently costs you bracketing and auto exposure. It is logged as a
  warning; check the log if a run comes back with one bracket.
- **A pixel format left in a shallower setting** by a previous session is inherited through the
  handover file, and costs range the dim end needs.
- **A saved instrument selection shadows `instruments.config`.** Removing an instrument from the
  config does not remove it from a window that once had it selected. Re-select the role and save.
- **A dark frame is only valid at its own exposure and gain.** That is why they are taken per
  bracket and per run rather than stored.
- **A frame median is not a substitute for a dark frame** for absolute readings. It is contaminated
  by the beam's own diffuse light, by an amount that changes with coupling, so it cannot be
  calibrated out. It is fine for a peak search, which only compares readings to each other.
