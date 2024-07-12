from LabExT.Instruments.InstrumentAPI import Instrument

class MultimeterAgilent34401A(Instrument):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def open(self):
        super().open()
        self._inst.query("*IDN?")
        """
        self._inst.read_termination = '\r\n'

        authentication = self._inst.query('open "anonymous"')
        ready = self._inst.query(" ")

        if authentication != 'AUTHENTICATE CRAM-MD5.' or ready != 'ready':
            raise InstrumentException('Authentication failed')"""
    
    def get_voltage_reading(self):
        pass