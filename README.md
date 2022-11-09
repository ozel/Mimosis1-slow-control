
## Python module for controlling the MIMOSIS-1 monolithic CMOS pixel detector chip via I2C
 
Developed for single event effect testing at the GSI X0 micro-beam line. 
Includes several read/write wrappers for direct access to general configuration, DAC, Multi Frame Emulation and readout test configuration registers.
Reading selected registers continously in a loop via runBitFlipSearch(), for SEE pencil beam scans or continous I2C connection checks.
With simulation mode for running without hardware and additional simulation introducing fake bit flips for testing purposes.

Requires at least python 3.8.
Further comments in the source code.

Author: Oliver Keller GSI/FAIR, 2022, o.keller ÄT gsi.de
BSD License

## Usage Examples:

### Without hardware, using simulation mode

```
from mimosis import mimosis
m = mimosis.Msis1(sim=True)

# enable verbose ouput
m.DEBUG = True

# write defaults from m.DAC dictionary 
m.writeDAC()

# read
m.readDAC()

# disable verbose ouput
m.DEBUG = False

# simulate bit-flips in DAC registers, continously reads every 1 ms
# 1% of reads will be affeted by a bit flip, see code of read()
m.runBitFlipSearch(0.001,m.readDAC,[m.writeDAC],simReadFlip=True)
# 1st parameter defines waiting time between continous reads
# 2nd parameter defines read function
# 3nd parameter defines set of optional write functions after bit-flips
# 4th parameter enables optional fake bit-flips for testing
```

### Using Raspberry Pi's I2C interface

Make sure I2C speed is not too high, up to 400 kbit/s works well with typical lab cabling.
This requires the smbus2 python module for I2C access.

```
from mimosis import mimosis
from smbus2 import SMBus

# tested with RPi4:
i2c = SMBus(3)
msis1 = mimosis.Msis1(chipid=1,fread=i2c.read_byte, fwrite=i2c.write_byte )

# optionally specify register settings as byte arrays parameter
# by default, values in m.DAC and m.GenConf are used, see code for guidance on settings
# default settings summary: 
# - internal PLL is disabled
# - external 320 MHz at CLK_RESCUE required
# - all 8 data outputs are on
# - frame marker output line enabled
m.writeGenConf()
m.writeDAC()

# example: enable only data output 0, set 0b10111 for all 8 ouputs
msis1.GenConf["OUTPUT"]=0b10100 
# write new setting
m.writeGenConf()

# modify DAC default thresholds
msis1.DAC["VCASNA"]=50
msis1.DAC["VCASNB"]=50
msis1.DAC["VCASNC"]=50
msis1.DAC["VCASND"]=50
msis1.DAC["VCASN2"]=165
msis1.DAC["VCLIP"]=75

# write new settings
m.writeDAC()

# continously check for bit-flips in MFE memory cells (SRAM) every 1 ms.
# write known pattern once before for reference
# if not specified via parameter array, default pattern is 0x55, see code
# ToDo: only writes lower bytes of MFE regions
m.writeMFE()
m.runBitFlipSearch(0.001,m.readMFE,[m.writeMFE],simReadFlip=False)

```