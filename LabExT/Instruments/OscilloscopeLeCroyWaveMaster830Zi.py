from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException

class OscilloscopeLeCroyWaveMaster830Zi(Instrument):
    def __init__(self, *args, **kwargs):
        super().__init__(*args,**kwargs)

    def open(self):
        super().open()