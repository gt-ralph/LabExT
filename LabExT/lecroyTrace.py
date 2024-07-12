import pyvisa
import os
from chardet import detect
import sys
from matplotlib import pyplot as plt
import struct
import numpy as np
import time

rm = pyvisa.ResourceManager()
scope1 = rm.open_resource('TCPIP0::ts404-17::inst0::INSTR')
scope1.write(f'COMM_FORMAT DEF9,BYTE,BIN')
format = scope1.query(f'COMM_FORMAT?').strip()
#scope1.query(f'HORIZ_INTERVAL?')
#print(format)


def inspect():
    traceStatus = scope1.query(f'C4:TRACE?')
    data = scope1.query(f'C4:INSPECT? "SIMPLE"').strip()

    offset = scope1.query(f'C4:OFFSET?').strip()
    offset = float(offset.split()[1])

    gain = scope1.query(f'C4:VDIV?').strip()
    gain = float(gain.split()[1])
    gain_mV = gain * 1000
        
    data = data.split()
    final_data = []
    for dp in data:
        dp = dp.strip()
        try:
            final_data.append(gain_mV*float(dp)-offset)
        except:
            continue        
    #plt.plot(final_data)
    #plt.show()
    return final_data

def trace():
    offset = scope1.query(f'C4:OFFSET?').strip()
    offset = float(offset.split()[1])

    gain = scope1.query(f'C4:VDIV?').strip()
    gain = float(gain.split()[1])
    gain_mV = gain * 1000
    
    #data = scope1.query_binary_values(f'C4:WAVEFORM?',is_big_endian = True)#,datatype='d')
    scope1.write(f'C4:WAVEFORM?')
    data = scope1.read_raw()
    """#plt.plot(data)
    filtered = list(filter(less_than_threshold,data))
    #plt.hist(data,500)
    plt.hist(filtered,100)
    plt.show()"""
    
    print(len(data))
    
    data = struct.unpack('>' + 'h' *(len(data)//2), data)
    decoded_values = [gain*point - offset for point in data]
    #plt.plot(decoded_values[184:])
    #plt.show()
    return decoded_values[184:]

def minimal_trace():
    scope1.write(f'C4:WAVEFORM?')
    data = scope1.read_raw()
    return data

#traceOut = trace()
#inspectOut = inspect()

#saveable = np.array(inspectOut)
#np.save('traceData',saveable)

# METADATA:
# power: -8 dbM
# frequency = 10 MHz


#plt.plot(inspectOut)
#plt.plot(traceOut)
#plt.show()
#trace()


times = []
for count in range(100):
    startTime = time.time()
    x = minimal_trace()
    endTime = time.time()
    diff = (endTime - startTime)*1000
    times.append(diff)   

print(len(times)) 

"""avgTimes = []
for count in range(100):
    starTime = time.time()
    avg = scope1.query('C4:PAVA? MEAN')
    #scope1.write('C4:PADL MEAN')
    endTime = time.time()
    diff = (endTime - startTime)*1000
    avgTimes.append(diff)

"""

startTime = time.time()
avg = scope1.query('C4:PAVA? MEAN')
endTime = time.time()
print((endTime-startTime)*1000)
plt.scatter(range(len(times)),times)
#plt.scatter(range(len(avgTimes)),avgTimes)
avgTime = np.mean(times)
plt.axhline(y = avgTime,linestyle='--',color='red')
plt.xlabel('Trace Count')
plt.ylabel('Trace Acquisition Time (ms)')
plt.show()

print(avgTime*1000)
#plt.savefig('traceTime_avg10ms')

scope1.close()



"""







THIS DOESNT WORK
#print(scope1.query('*IDN?').strip())
scope1.write(f'C4:WAVEFORM?')
raw_data = scope1.read_raw()
raw_data.decode()
#raw_data = str(raw_data,'utf-8')
#raw_data.encode('utf-8','ignore')
detected_encoding = detect(raw_data)
print(detected_encoding)


# DECODE
header_length = 2 + int(raw_data[1])
print(type(raw_data))
data_length = len(range(raw_data[2:header_length]))
print(data_length)
#waveform_data = raw_data[header_length:header_length+data_length]

#raw_data = raw_data.decode('utf-8')


scope1.close()
"""