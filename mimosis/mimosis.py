#####################################################
# Phyton module for controlling MIMOSIS-1 via I2C
# 
# Developed for single event effect testing 
# at GSI X0 micro-beam line. Reading selected registers 
# continously in a loop via runBitFlipSearch().
# Several read/write wrappers for direct access to 
# general configuration, DAC, Multi Frame Emulation 
# and Readout Test Configuration registers.
# Includes simulation mode for testing without hardware 
# and additional simulation introducing fake bit-flips
# for testing purposes at 1% of reads.
#
# Requirements: 
# - python 3.8 or higher for ordered dicts, unhexlify features
#
# ToDo:
# - put MicrobeamSubscriberSocket class in separate modukle
# - put bitflip search functionality in separate module
# - few FIXMEs below, mostly unused code
#
# Author:
# Oliver Keller GSI/FAIR | 2022 | o.keller ÄT gsi.de
# BSD License

from sys import stdout
import random, binascii, time
import asyncio
    
class Msis1: 
    
    DEBUG = False # enable for printing out individual read/write I2C transactions

    def __init__(self, sim=False, chipid=1,fread=None, fwrite=None, beamControl=False):
        # sim = enable simulation mode, no hardware required (fread/fwrite will not be used)
        # chipid = specifiy Mimosis-1 chip id
        # fread = specify I2C read function
        # fwrite = specify I2C write function
        # beamControl = optionally enable
        self.chipid = chipid

        if fwrite is not None:
           self.chipwrite = fwrite
        else:
            if sim is True:
                self.chipwrite = self.writeSim
        
        if fread is not None:
           self.chipread = fread
        else:
            if sim is True:
                self.chipread = self.readSim
        
        self.conf = {}
        
        # GenConf and DAC values based on Msis1_no_pll_gilles.bin from 12.03.2021
        self.GenConf = {'RUNMODE'   : 0x40, # enable CLKRESCUE pad termination
                        'TRIMDAC'   : 0x6E, # reset default
                        'INJCURR'   : 0x00, # reset default
                        'INJVOLT1'  : 0x00, # reset default
                        'INJVOLT2'  : 0x00, # reset default
                        'MONCURR'   : 0x00, # reset default
                        'MONVOLT'   : 0x00, # reset default
                        'CLKGEN1'   : 0x01, # select rescue clock (320 MHz)
                        'CLKGEN2'   : 0x01, # DIS_LOCK_GATING = 1
                        'PLL'       : 0x16, # disable PLL VCO and power regulator
                        'PLLLOCK'   : 0x00, # disable PLL lock detection
                        'MONTEMP'   : 0x00, # reset default
                        'SLVSTX'    : 0x15, # reset default
                        'SLVSRX'    : 0x00, # !very low input bias for CLK & CLKRESCUE pads! (from Gille's config, boderline but OK according to Fred)
                        'OUTPUT'    : 0x17, # !enable all 8 outputs and data marker!
                        'MONPWR'    : 0x00, # reset default
        }
        self.DAC =    { 'IBIAS'     : 64,
                        'ITHR'      : 52,
                        'IDB'       : 28,
                        'VRESET'    : 171,
                        'VPL'       : 70,
                        'VPH'       : 85,
                        'VPH_FINE'  : 0,
                        'VCASP'     : 67,
                        'VCASNA'    : 1,
                        'VCASNB'    : 1,
                        'VCASNC'    : 1,
                        'VCASND'    : 1,
                        'VCASN2'    : 83,
                        'VCLIP'     : 50,
                        'IBUFBIAS'  : 125
        }
        
        self.beamControl = beamControl
        self.confLoaded = False
        self.bitFlipFound = False
        self.bitFlipResult = []
        self.simReadFlip = False
        self.lastRead = bytearray(16)
        
        self.bitcounts = bytes(bin(x).count("1") for x in range(256)) #look-up table for number of 1-bits in a byte
       
        
        
        if sim is True:
            # all bits zero, which is not the default chip config in reality!
            self.simRegs = []
            for i in range(256):
                self.simRegs.append([])
                for j in range(256):
                    self.simRegs[i].append([0x00,0x00])
        

        cmdIds = enumerate( ['INSTR','ADD_MSB','ADD_LSB','WR','RD','WR_IND','RD_IND','WR_OFF','RD_OFF'], start=1) 
        self.CMDID = dict((j,i) for i,j in cmdIds)

        self.ADDRS = {"GenConf"  : [0b0000_0000,0b0010_0000],
                      "DAC"      : [0b0000_0000,0b0100_0000],
                      "SeqConf"  : [0b0000_0000,0b0110_0000],
                      "PixCtrl"  : [0b0000_0000,0b1000_0000],
                      "Mon"      : [0b0000_0000,0b1110_0000],
                      "MFE"      : [0b1000_0000,0b0000_0000],
                      "AnaPixSel": [0b1000_0000,0b0010_0000],
                      "RoTstConf": [0b1000_0000,0b0100_0000],
        }

        self.tcpSocket=MicrobeamSubscriberSocket()
        if self.beamControl is False:
            # bogus defaults for the log file
            self.tcpSocket.scanId = 0 
            self.tcpSocket.scan_x = 0 
            self.tcpSocket.scan_y = 0 
        
        
    def write(self, addr, *args):
        # only single byte writing for now: address byte + 1 payload byte in args
        return self.chipwrite(addr, *args)
    
    def read(self, addr, *args):
        # only single byte reading for now, args unused
        result = self.chipread(addr, *args)
        if self.simReadFlip is True:
            if random.randint(0,999) == 0:
                #flip a random bit with 0.1% chance
                rbit = (result << random.randint(0,7)) & 0x80 # select bit
                result = result ^ rbit # flip it!
        return result
                
    def writeSim(self, addr, *args):
        if self.DEBUG: 
            print("< write ", hex(addr), ": ", end='');
            for i in args:
                print(hex(i), end=' ')
            print()
            
        if addr == self.getCmdByte("ADD_LSB") and len(args)==1:
            self.simAddrLSB = args[0]
        elif addr == self.getCmdByte("ADD_MSB") and len(args)==1:
            self.simAddrMSB = args[0]            
        elif addr == self.getCmdByte("WR"):
             self.simRegs[self.simAddrMSB][self.simAddrLSB]=args
        return len(args)

    def readSim(self, addr, *args):
        try:
            length=len(*args)
        except:
            length=1
        if self.DEBUG:
            print(">  read ", hex(addr), ":", length, "byte(s)" )
        if addr == self.getCmdByte("RD"):
            reg=self.simRegs[self.simAddrMSB][self.simAddrLSB]
            if self.DEBUG:
                print(">> ", end="")
                for i in range(length):
                    print(hex(reg[i]), end=" ")
                print()
            if length == 1:
                return reg[0]
            else:
                return reg[:length]
        else:
            print("address mismatch!")
            return len(args)
    
    def getCmdByte(self, cmdId):
        # assemble I2C address byte from chip id and command id
        cmd = self.CMDID[cmdId]
        byte = (0b111 & self.chipid) << 4 | (0b1111 & cmd)
        return byte

    def getBytesFromDict(self, d):
        # convert human readable dictionaries of general configuration 
        # and DAC registers (self.GenfConf & self.DAC) into byte arrays
        ba = bytearray(len(d))
        # this expects ordered dicts which are default as of python 3.7
        for i, value in enumerate(d.values()):
            ba[i]=value
        return ba
   
    def rwReg16w(self, type, buf, mode='r'):
        # can be used for GeneralConf, DAC & Monitoring registers
        # writes 15 or 16 bytes into registers
        if type not in ("GenConf", "DAC", "Mon"):
            print("! wrong register type for this method")
            return False
      
        if buf is None:
            if self.confLoaded is not True:
                # not implemented for now
                print("! neither byte array nor loaded config found")
                return False
            buf = self.conf[type]["W8"]
        elif type == "GenConf" and len(buf) < 16: 
            print("! buf too small" )
            return False
        elif (type == "DAC" or type == "Mon") and len(buf) < 15: 
            print("! buf too small" )
            return False
                  
        writeByte=self.ADDRS[type][0]
        self.write(int(self.getCmdByte("ADD_MSB")), writeByte)

        for i in range(len(buf)):
            writeByte=(self.ADDRS[type][1] & 0b1111_0000) | i
            self.write(self.getCmdByte("ADD_LSB"), writeByte)
            if mode == 'r':
                buf[i] = self.read(self.getCmdByte("RD"))
            else:
                writeByte=buf[i]
                self.write(self.getCmdByte("WR"),writeByte)
        return len(buf)
        
    def readGenConf(self):
        readBytes = bytearray(16)
        self.rwReg16w("GenConf", readBytes, 'r')
        self.lastRead = readBytes
        return readBytes

    def readDAC(self):
        readBytes = bytearray(15)
        self.rwReg16w("DAC", readBytes, 'r')
        self.lastRead = readBytes
        return readBytes

    def readMon(self):
        readBytes = bytearray(15)
        self.rwReg16w("DAC", readBytes, 'r')
        self.lastRead = readBytes
        return readBytes

    def writeGenConf(self, writeBytes=None):
        if writeBytes is None:
            writeBytes = self.getBytesFromDict(self.GenConf)
        return self.rwReg16w("GenConf",writeBytes, 'w')

    def writeDAC(self, writeBytes=None):
        if writeBytes is None:
            writeBytes = self.getBytesFromDict(self.DAC)
        return self.rwReg16w("DAC",writeBytes, 'w')

    def writeMon(self, writeBytes=None):
        return self.rwReg16w("DAC",writeBytes, 'w')
    
    def rwPixCtrl(self, mask, value, broadcast=True, mode='r'):
        # access Pixel Control Register
        # FXIME: needs further tesing and read/write() wrappers
        if broadcast is not True:
            print("! only broadcast mode supported")
         
        writeByte=0b0100_0000 #BCAS = 1, region addr = 0
        self.write(self.getCmdByte("ADD_MSB"), writeByte)

        writeByte=(self.ADDRS["PixCtrl"][1] & 0b1110_0000) | (mask & 0b1_1111)
        self.write(self.getCmdByte("ADD_LSB"), writeByte)
        if mode == 'r':
            value = self.pread(self.getCmdByte("RD"))
            return value
        else:
            writeByte=value
            self.write(self.getCmdByte("WR"),writeByte)
            return 1
       
    def rwRoTstConf(self, buf, mode='r'):
        # access Readout Test Configuration register
        writeByte=self.ADDRS["RoTstConf"][0]  #0x80
        self.write(self.getCmdByte("ADD_MSB"), writeByte)
        
        offset=0x40
        for i in range(20):
            writeByte=offset
            self.write(self.getCmdByte("ADD_LSB"), writeByte)
            if mode == 'r':
                buf[i] = self.read(self.getCmdByte("RD"))
            else:
                writeByte=buf[i]
                self.write(self.getCmdByte("WR"),writeByte)
            offset += 1
        return len(buf)
    
    def readRoTstConf(self):
        readBytes = bytearray(20)
        self.rwRoTstConf(readBytes, 'r')
        return readBytes
        
    def writeRoTstConf(self,writeBytes=None):
        if writeBytes is None:
            writeBytes = bytearray(b'\x55'*20)
        self.rwRoTstConf(writeBytes, 'w')
        return writeBytes

    def readMFE(self):
        readBytes = bytearray(64*8)
        self.rwMFE(readBytes, 'r')
        return readBytes
        
    def writeMFE(self,writeBytes=None):
        if writeBytes is None:
            writeBytes = bytearray(b'\x55'*64*8)
        self.rwMFE(writeBytes, 'w')
        return writeBytes
        
    def rwMFE(self, buf, mode='r'):
        # access MultiFrameEmulation memory cells (SRAM)
        # FIXME: high byte is missing
        writeByte=0
        for region in range(64):
            writeByte=(self.ADDRS["MFE"][0] & 0b1000_0000) | (region & 0b11_1111)
            self.write(self.getCmdByte("ADD_MSB"), writeByte)
            for frame in range(8):
                writeByte=(self.ADDRS["MFE"][1] & 0b1111_1000) | (frame & 0b111)
                self.write(self.getCmdByte("ADD_LSB"), writeByte)
                if mode == 'r':
                    buf[region*8 + frame] = self.read(self.getCmdByte("RD"))
                else:
                    writeByte=buf[region*8 + frame]
                    self.write(self.getCmdByte("WR"),writeByte)
        return len(buf)    

    def runBitFlipSearch(self, interval, function, update=None,simReadFlip=False):
        # Starts continous register read loop as asyncio co-routine (~lightweight thread)
        # interval = waiting time between consecutive reads in seconds as float number
        # function = desired read function for continous regsister checking
        # update = optional list/array of functions for re-writing known register settings after bit-flip
        # simReadFlip = if True, introduce fake bit flips for testing purposes!
        self.simReadFlip=simReadFlip
        loop = asyncio.get_event_loop()
        #func = partial(function, *args)
        loop.run_until_complete(self.__asyncBitFlips(function, interval, update))
        return self.bitFlipResult

    async def __asyncBitFlips(self, function, interval, update):
        loop = asyncio.get_event_loop()
        if self.beamControl is True:
            await self.tcpSocket.connect('localhost')
            readScanPos = loop.create_task(self.__readScanPos())
            checkBitFlipLoop = loop.create_task(self.__checkBitFlipLoop(function, interval,update))
            await asyncio.gather(checkBitFlipLoop, readScanPos)
        else:
            checkBitFlipLoop = loop.create_task(self.__checkBitFlipLoop(function, interval,update))
            await asyncio.gather(checkBitFlipLoop)
        return
    
    async def __readScanPos(self):
        while True:
            msg = await self.tcpSocket.read_msg()
            if self.DEBUG is True:
                print('tcpSocket msg:', msg)
  
    async def __checkBitFlipLoop(self, function, interval, update):
        self.lastRead = function()
        reference = self.lastRead
        
        counter = 0
        while True:
            #print(".",end='')
            self.lastRead = function() 
            onesInReference=self.onesInBytes(reference)
            counter +=1
            for i in range(len(self.lastRead)):
                if reference[i] != self.lastRead[i]:
                    tstamp=self.tstr(time.localtime())
                    self.bitFlipFound=True
                    self.bitFlipResult=[reference, self.lastRead]
                    print(tstamp,"\t", counter, "\t", 
                          str(function.__name__), '\t', 
                          onesInReference , "\told\t", 
                          self.tcpSocket.scanId, ':', self.tcpSocket.scan_x, ':', self.tcpSocket.scan_y, "\t", sep='', end="")
                    self.baprint(reference)
                    
                    print(tstamp, "\t", counter,"\t", 
                          str(function.__name__), '\t', 
                          self.onesInBytes(self.lastRead) , "\t",
                          "new","\t", 
                          self.tcpSocket.scanId, ':', self.tcpSocket.scan_x, ':', self.tcpSocket.scan_y, "\t", sep='', end="")
                    self.baprint(self.lastRead)
                    stdout.flush()
                    if update is not None:
                        for u in update:
                            u()
                            if self.DEBUG is True:
                                print("! updating at", counter, u, )
                    else:
                        return
                    break
              
            await asyncio.sleep(interval)
    
    async def __hitSimulator(self):
        # not used for now. optional simulation of bit flips was moved to read()
        while self.bitFlipFound is not True:
            interval=random.randrange(1,10)
            await asyncio.sleep(interval/1000)
            randAddr = random.randrange(0,len(self.simRegs))
            randReg = random.randrange(0,len(self.simRegs[0]))
            self.simRegs[randAddr][randReg]=[0xff,0xff]
    
    def updateRegs(self):
        # helper function to update several registers at once for runBitFlipSearch() 
        self.writeGenConf()
        self.writeDAC()
        print(">>> updated General Config und DACs")

    def baprint(self,byteArray):
        # nice hexlified printing of binary arrays
        if not isinstance(byteArray, (tuple, list)):
           byteArray=[byteArray]
        for array in byteArray:
            print(binascii.hexlify(array, b' '),end="")
            print("\t")
            
    def tstr(self,t):
        # format timestamp string
        return str(t[3]).zfill(2) +':'+ str(t[4]).zfill(2)+':'+str(t[5]).zfill(2) +' '+ str(t[0]) +'/'+ str(t[1]).zfill(2) +'/'+ str(t[2]).zfill(2) 
        
    def onesInBytes(self,buf):
        # sum-up number of bits set to 1 in buf[] bytes
        counter=0
        for byte in buf:
            counter += self.bitcounts[byte]
        return counter

    # def loadChipConf(self,file=None):
    # FIXME: needs a re-write if needed at all...
    #     if file:
    #         conf_name = file
    #     else:
    #         conf_name = "chip0_gilles.json" 
    #     with open(conf_name, 'r') as f:
    #         self.conf = json.load(f)["RegBase"]["Regs"]
    #     self.confLoaded = True
    
    # def rwSeqConf(self, mode, pattern):
    # FIXME: needs a re-write if needed at all...
    #     if isinstance(pattern list):
    #         print("! only same pattern for all registers")
    #     writeBytes= bytearray(1)
    #     writeBytes[0]=self.ADDRS["SeqConf"][0]
    #     self.chipwrite(self.getCmdByte("ADD_MSB"), writeBytes)
    #     offsets=[0x60,0x70,0x61,0x71,0x62,0x72,0x63,0x73,0x64,0x74,0x65,0x75,0x66,0x76,0x68,0x78,0x69,0x79,0x7A,0x7B,0x7C]
    #     for i in len(offsets):
    #         # PixCtrl LSB reg
    #         writeBytes[0]=(self.ADDRS["SeqConf"][1] & 0b1110_0000) | offsets[i]
    #         self.chipwrite(self.getCmdByte("ADD_LSB"), writeBytes)
    #         self.chipwrite(self.getCmdByte("WR"),pattern)
    #         # PixCtrl MSB reg
    #         writeBytes[0]=(self.ADDRS["SeqConf"][1] & 0b1110_0000) | (0x100 + offsets[i])
    #         self.chipwrite(self.getCmdByte("ADD_MSB"), writeBytes)
    #         self.chipwrite(self.getCmdByte("WR"),pattern)
    #     offsets=[0x7D,0x7E,0x7F]
    #     for i in len(offsets):
    #         # PixCtrl LSB reg
    #         writeBytes[0]=(self.ADDRS["SeqConf"][1] & 0b1110_0000) | offsets[i]
    #         self.chipwrite(self.getCmdByte("ADD_LSB"), writeBytes)
    #         self.chipwrite(self.getCmdByte("WR"),pattern)
    #     return 49
            
class MicrobeamSubscriberSocket:
    # reads current beam position over TCP for SEE/pencil beam scans
    # only required for runBitFlipSearch()
    
    DEBUG = False
        
    def __init__(self):
        self.reader=None
        self.writer=None
        self.scanId=None
        self.scan_x=None
        self.scan_y=None

    async def connect(self, host):
        print("Starting TCP client, receiving scan coordinates from ", host)

        self.reader, self.writer = await asyncio.open_connection(
        host, 8188)

    async def read_msg(self):
        line=await self.reader.readline()
        if not line:
            return
        msg=line.decode('utf8')
        words=msg.split(' ')
        if words[0] == 'start_run' :
            if self.DEBUG is True:
                print(words[0], words[1])
            self.scanId=int(words[1])
        elif words[0] == 'stop_run':
            if self.DEBUG is True:
                print(words[0])
        elif words[0] == 'pos':
            if self.DEBUG is True:
                print('pos',words[1],words[2])
            self.scan_x=int(words[1])
            self.scan_y=int(words[2])
        return msg            