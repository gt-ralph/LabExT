# Lookup environment variable OSCOPE_IP and use it as the resource
# name or use the TCPIP0 string if the environment variable does
# not exist
from oscope_scpi import Oscilloscope
from os import environ

resource = environ.get('OSCOPE_IP', 'TCPIP0::169.254.187.212::INSTR')
pn = ""
fn = pn + "/"
# create your visa instrument
instr = Oscilloscope(resource)

# Upgrade Object to best match based on IDN string
instr = instr.getBestClass()

# Open connection to instrument
instr.open()

# set to channel 1
#
# NOTE: can pass channel to each method or just set it
# once and it becomes the default for all following calls. If pass the
# channel to a Class method call, it will become the default for
# following method calls.
channels = ['1', '2', '3', '4']

k = 0 
for k in range(1):
    for i in channels:
        instr.channel = i

        # Enable output of channel, if it is not already enabled
        if not instr.isOutputOn():
            instr.outputOn()

        # Install measurements to display in statistics display and also
        # return their current values here
        print('Ch. {} Settings: {:6.4e} V  PW {:6.4e} s\n'.
                format(instr.channel, instr.measureVoltAverage(install=True),
                            instr.measurePosPulseWidth(install=True)))

        # Add an annotation to the screen before hardcopy
        instr.annotate("{} {} {}".format('Example of Annotation','for Channel',instr.channel))

        # Make sure the statistics display is showing for the hardcopy
        instr.measureStatistics()

        # Save a hardcopy of the screen to file 'outfile.png'
        instr.waveform(fn + 'channel' + int(i) + '_' + 'file_' + int(k+1) + '.mat')

# Turn off the annotation
instr.annotateOff()
    
# turn off the channel
instr.outputOff()

# return to LOCAL mode
instr.setLocal()

instr.close()