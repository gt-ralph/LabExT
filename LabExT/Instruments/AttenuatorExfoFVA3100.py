from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException

class AttenuatorExfoFVA3100(Instrument):
    def __init__(self, *args, **kwargs):
        super().__init__(*args,**kwargs)

    def open(self):
        super().open()