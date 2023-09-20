#!/usr/bin/env python
 
# probably not all these required some are legacy and no longer used.
from dbus.mainloop.glib import DBusGMainLoop

try:
  import gobject  # Python 2.x
except:
  from gi.repository import GLib as gobject # Python 3.x

import dbus
import dbus.service
import inspect
import platform
from threading import Timer
import argparse
import logging
import sys
import os
import json
import time, threading
import requests # for http GET
import configparser # for config/ini file

from pymodbus.client.sync import ModbusTcpClient as ModbusClient

import time
import ctypes

log = logging.getLogger("DbusKaco")

sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-modem'))
from vedbus import VeDbusService


# ----------------------------------------------------------------
# Keine Ahnung ob die wichtig ist.
VERSION     = "0.1"
# ----------------------------------------------------------------



def _getConfig():
    global globalConfig
    if globalConfig == 0:
        config = configparser.ConfigParser()
        config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
        log.debug("config.read")
        globalConfig = config

    return globalConfig

def _get_string(regs):
    numbers = []
    for x in regs:
        if (((x >> 8) & 0xFF) != 0):
            numbers.append((x >> 8) & 0xFF)
        if (((x >> 0) & 0xFF) != 0):
            numbers.append((x >> 0) & 0xFF)
    return ("".join(map(chr, numbers)))

def _get_signed_short(regs):
    return ctypes.c_short(regs).value

def _get_scale_factor(regs):
    log.debug(f"_get_scale_factor({regs})")
    return 10**_get_signed_short(regs)

def _get_scaled_value(val, scaleVal):
    log.debug("getting scaled value raw: " + str( val ) )
    log.debug("getting scaled value signed short: " + str( _get_signed_short(val) ) )
    log.debug("getting scaled value scale: " + str( scaleVal) )
    log.debug("getting scaled value scale: " + str( _get_scale_factor(scaleVal)  ) )
    return val * _get_scale_factor(scaleVal)


# get information from sunspec model
def _parse_sunspec_model(id, content, len):
    global M120_maxPower, M123_powerLimitScaleFactor
    try:
        log.debug(f"parseModel {str(id)} len: {str(len)}")

        if id == 1:
            log.info("Model: " + _get_string(content.registers[0:15]))

        elif id == 120:
            if( len > 4 ):
                log.info("120.DERTyp: " + str(content.registers[0]) )
                log.info("120.maxPower: " + str( _get_scaled_value(content.registers[1], content.registers[2]) ) )
                log.info("120.VARtg: " + str( _get_scaled_value(content.registers[3], content.registers[4]) ) )
                M120_maxPower = _get_scaled_value(content.registers[1], content.registers[2])
                
        elif id == 123:
            if len > 8:
                log.info("123.WMaxLim_Ena " + str(content.registers[7]))
                log.info("123.OutPFSet " + str(content.registers[8]))

            if len > 23:
                log.info("123.WMaxLimPct_SF: " + str ( _get_scale_factor( content.registers[21] ) ) )
                log.info("123.OutPFSet_SF: " + str ( _get_scale_factor( content.registers[22] ) ) )
                log.info("123.VArPct_SF: " + str ( _get_scale_factor( content.registers[23] ) ) )
                
                M123_powerLimitScaleFactor = _get_scale_factor( content.registers[23] )
                log.info("123_powerLimitScale " + str(M123_powerLimitScaleFactor) )

# mPowerLimitPct * 120.maxPower
# mPowerLimitPct = value / 120.maxPower
# pct = mPowerLimitPct * powerLimitScale
# modbusClient.write_registers(sunspecModels[123]['offset'] + 5, [pct, 0, 300, 0, 1] , unit=UNIT)   (anderes offset!)

    except Exception as e:
        log.error('exception in _parseSunspecModel')
        log.exception(str(e))

    return True


# detect sunspec model, start at baseRegister, usually 40000
def _detect_sunspec_modules(baseRegister):
    
    sunspecModels = {}
    log.info(f'Starting Sunspec Model detection @ {baseRegister}')
    try:
        startRegister = baseRegister

        sunspec_header = modbusClient.read_holding_registers(startRegister, 2, unit=SERVER_UNIT)
        log.debug(f'header @ {sunspec_header}')
    except Exception as e:
        log.error('exception in _get_sunspec_modules')
        log.exception(str(e))


    if ( sunspec_header.registers[0] == 0x5375 and sunspec_header.registers[1] == 0x6e53 ):
        log.info("Found valid sunspec header: " + str(hex(( (sunspec_header.registers[0] << 16) + sunspec_header.registers[1] ))))
        startRegister += 2
        
        while True: 
            header = modbusClient.read_holding_registers( startRegister, 2, unit=SERVER_UNIT)
            if header.isError():
                log.error(f"no module header for register {startRegister}")

            modelID = header.registers[0]
            modelLen = header.registers[1]

            if( (modelID == 0xFFFF) or (modelLen == 0) ):
                log.debug(f"End of map model found")
                break

            startRegister += 2
            
            log.debug(f"Sunspec modelID {modelID} modelLen {modelLen} startAddress: {startRegister}")
        
            content = modbusClient.read_holding_registers( startRegister, modelLen, unit=SERVER_UNIT)
            if content.isError():
                log.error(f"no module content for model {modelID}")

            sunspecModels[modelID] = { "offset": startRegister, "length": modelLen }
            log.info(f"Sunspec Model {modelID} Len {modelLen} Offset: {startRegister} found")
            
            try:
                _parse_sunspec_model(modelID, content, modelLen)
            except Exception as e:
                log.error('exception in _get_sunspec_modules')
                log.exception(str(e))

            startRegister += modelLen
        
        return sunspecModels
    else:
        log.error("Sunspec header not found " + str(hex(( (sunspec_header.registers[0] << 16) + sunspec_header.registers[1] ))))
        exit(1)



def _get_victron_pv_state(state):
    log.debug('get_victron_pv_state: ' + str(state))
    if (state == 1):        # Device is not operating
        return 0 
    elif (state == 3):      # Device is staring up
        return 1
    elif (state == 4):      # Device is auto tracking maximum power
        return 11
    elif (state == 5):      # Device is operating at reduced power output
        return 12
    elif (state == 7):      # One or more faults exist
        return 10
    else:
        return 8            # Device is in standby mode
        
# State 2 = Device is sleeping / auto-shutdown
# State 6 = Device is shutting down        

# Again not all of these needed this is just duplicating the Victron code.
class SystemBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SYSTEM)
 
class SessionBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SESSION)
 
def dbusconnection():
    return SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else SystemBus()
 

def _refresh_power_limit_event():
    global refresh_timer, PowerLimitPct
    log.info('_refresh_power_limit_event {PowerLimitPct}')

    refresh_timer.cancel()
    _set_power_limit(PowerLimitPct)
    return True

def _disable_power_limit():
    global refresh_timer
    log.info("_disable_power_limit")
    maxPowerData = [0]
    log.info(f'modbusClient.write_registers(sunspecModels[123]["offset"] + 7, {maxPowerData} , unit={SERVER_UNIT})')
    modbusClient.write_registers(sunspecModels[123]['offset'] + 7, maxPowerData , unit=SERVER_UNIT)
    
    if refresh_timer != 0:
        if refresh_timer.is_alive():
            refresh_timer.cancel()
            log.info("refresh_timer canceled")
    


def _set_power_limit(percentLimit):
    global refresh_timer, PowerLimitPct

    log.info(f"_set_power_limit({percentLimit}) M123_powerLimitScaleFactor: {M123_powerLimitScaleFactor}")
    
    try:
        PowerLimitPct = percentLimit    # in globaler Variable merken, damit wir es ggf. per Timer wieder setzen koennen
        if M123_powerLimitScaleFactor > 0:
            scaledPercent = percentLimit / M123_powerLimitScaleFactor
        else:
            log.error("M123_powerLimitScaleFactor was 0 or less")
            scaledPercent = 100

        maxPowerData = [int(scaledPercent), 0, 300, 0, 1]
        log.info(f'_set_power_limit maxPowerData {maxPowerData}')
    
        log.debug(f'_set_power_limit write_registers(sunspecModels[123]["offset"] + 3, {maxPowerData} , unit={SERVER_UNIT})')
        modbusClient.write_registers(sunspecModels[123]['offset'] + 3, maxPowerData , unit=SERVER_UNIT)

    except Exception as e:
        log.error('exception in _set_power_limit.')
        log.exception(str(e))

    if refresh_timer != 0:
        if refresh_timer.is_alive():
            refresh_timer.cancel()
            log.info("refresh_timer canceled")

    if percentLimit > 0:
        refresh_timer = threading.Timer(300 / 2, _refresh_power_limit_event)
        refresh_timer.start()
        log.info("refresh_timer started")

def _maxpower_change(path, newvalue):
    log.info(f"_maxpower_change {path}, maxPower: {newvalue}")

def _powerlimit_change(path, newvalue):
    global refresh_timer
    
    log.info(f"_powerlimit_change {path}, maxPower: {newvalue}")
    try:
        
        if M120_maxPower == 0:
            return True
        
        pct = int(round((int(newvalue) / M120_maxPower) * 100,0))
        log.info("_powerlimit_change pct  " + str(pct))

        _set_power_limit(pct)

    except Exception as e:
        log.error('exception in _powerlimit_change.')
        log.exception(str(e))

    return True

def _update():
    log.debug('update()')
    try:
        regs = modbusClient.read_holding_registers( sunspecModels[103]["offset"] , sunspecModels[103]["length"], unit=SERVER_UNIT)
        if regs.isError():
            log.error(f'regs.isError: {regs}')
            sys.exit()
        else:
           log.debug('update, read module 103: ' + str(sunspecModels[103]))
           
           sf = _get_scale_factor(regs.registers[4])
           dbusservice['pvinverter.pv0']['/Ac/L1/Current'] = round(regs.registers[1] * sf, 2)
           dbusservice['pvinverter.pv0']['/Ac/L2/Current'] = round(regs.registers[2] * sf, 2)
           dbusservice['pvinverter.pv0']['/Ac/L3/Current'] = round(regs.registers[3] * sf, 2)
           dbusservice['pvinverter.pv0']['/Ac/Current'] = round(regs.registers[1] * sf + regs.registers[2] * sf + regs.registers[3] * sf , 2)

           
           sf = _get_scale_factor(regs.registers[11])
           dbusservice['pvinverter.pv0']['/Ac/L1/Voltage'] = round(regs.registers[8] * sf, 2)
           dbusservice['pvinverter.pv0']['/Ac/L2/Voltage'] = round(regs.registers[9] * sf, 2)
           dbusservice['pvinverter.pv0']['/Ac/L3/Voltage'] = round(regs.registers[10] * sf, 2)
           
           sf = _get_scale_factor(regs.registers[13])
           acpower = _get_signed_short(regs.registers[12]) * sf
           dbusservice['pvinverter.pv0']['/Ac/Power'] = acpower
           dbusservice['pvinverter.pv0']['/Ac/L1/Power'] = round(_get_signed_short(regs.registers[1]) * _get_signed_short(regs.registers[8]) * _get_scale_factor(regs.registers[11]) * _get_scale_factor(regs.registers[4]), 2)
           dbusservice['pvinverter.pv0']['/Ac/L2/Power'] = round(_get_signed_short(regs.registers[2]) * _get_signed_short(regs.registers[9]) * _get_scale_factor(regs.registers[11]) * _get_scale_factor(regs.registers[4]), 2)
           dbusservice['pvinverter.pv0']['/Ac/L3/Power'] = round(_get_signed_short(regs.registers[3]) * _get_signed_short(regs.registers[10]) * _get_scale_factor(regs.registers[11]) * _get_scale_factor(regs.registers[4]), 2)
           
           sf = _get_scale_factor(regs.registers[24])
           dbusservice['pvinverter.pv0']['/Ac/Energy/Forward']    = round(float((regs.registers[22] << 16) + regs.registers[23]) * sf / 1000,3)
           dbusservice['pvinverter.pv0']['/Ac/L1/Energy/Forward'] = round(float((regs.registers[22] << 16) + regs.registers[23]) * sf / 3 / 1000,3)
           dbusservice['pvinverter.pv0']['/Ac/L2/Energy/Forward'] = round(float((regs.registers[22] << 16) + regs.registers[23]) * sf / 3 / 1000,3)
           dbusservice['pvinverter.pv0']['/Ac/L3/Energy/Forward'] = round(float((regs.registers[22] << 16) + regs.registers[23]) * sf / 3 / 1000,3)
           
           dbusservice['pvinverter.pv0']['/StatusCode'] = _get_victron_pv_state(regs.registers[36])
           dbusservice['pvinverter.pv0']['/ErrorCode'] = regs.registers[37]

           sf = _get_scale_factor(regs.registers[35])
           dbusservice['adc-temp0']['/Temperature'] = round(regs.registers[31] * sf, 2)

           # if ((regs.registers[36] == 5) & (acpower > 100)):
               # dbusservice['digitalinput0']['/State'] = 3
               # dbusservice['digitalinput0']['/Alarm'] = 2
           # else:
               # dbusservice['digitalinput0']['/State'] = 2
               # dbusservice['digitalinput0']['/Alarm'] = 0
    except Exception as e:
        log.error('exception in _update.')
        log.exception(str(e))
        sys.exit()

    return True
 
# Here is the bit you need to create multiple new services - try as much as possible timplement the Victron Dbus API requirements.
def new_service(base, type, physical, id, instance):
    self =  VeDbusService("{}.{}.{}_id{:02d}".format(base, type, physical,  id), dbusconnection())

    # Create the management objects, as specified in the ccgx dbus-api document
    self.add_path('/Mgmt/ProcessName', __file__)
    self.add_path('/Mgmt/ProcessVersion', 'Unknown version, and running on Python ' + platform.python_version())
    self.add_path('/Connected', 1)  
    self.add_path('/HardwareVersion', 0)

    _kwh = lambda p, v: (str(v) + 'kWh')
    _a = lambda p, v: (str(v) + 'A')
    _w = lambda p, v: (str(v) + 'W')
    _v = lambda p, v: (str(v) + 'V')
    _c = lambda p, v: (str(v) + 'C')

    # Create device type specific objects
    if physical == 'grid':
        if modbusClient.is_socket_open():
            # read registers, store result in regs list
            regs = modbusClient.read_holding_registers(sunspecModels[1]["offset"], sunspecModels[1]["length"], unit=SERVER_UNIT)
            if regs.isError():
                log.error(f'regs.isError: {regs}')
                sys.exit()
            else:
                self.add_path('/DeviceInstance', instance)
                self.add_path('/FirmwareVersion', _get_string(regs.registers[40:47]))
                self.add_path('/DataManagerVersion', VERSION)
                self.add_path('/Serial', _get_string(regs.registers[48:63]))
                self.add_path('/Mgmt/Connection', CONNECTION)
                self.add_path('/ProductId', 16) # value used in ac_sensor_bridge.cpp of dbus-cgwacs
                self.add_path('/ProductName',  _get_string(regs.registers[0:15])+" "+_get_string(regs.registers[16:31]))
                self.add_path('/CustomName', "Grid meter " +_get_string(regs.registers[32:39]))
                self.add_path('/Ac/Power', None, gettextcallback=_w)
                self.add_path('/Ac/L1/Voltage', None, gettextcallback=_v)
                self.add_path('/Ac/L2/Voltage', None, gettextcallback=_v)
                self.add_path('/Ac/L3/Voltage', None, gettextcallback=_v)
                self.add_path('/Ac/L1/Current', None, gettextcallback=_a)
                self.add_path('/Ac/L2/Current', None, gettextcallback=_a)
                self.add_path('/Ac/L3/Current', None, gettextcallback=_a)
                self.add_path('/Ac/L1/Power', None, gettextcallback=_w)
                self.add_path('/Ac/L2/Power', None, gettextcallback=_w) 
                self.add_path('/Ac/L3/Power', None, gettextcallback=_w) 
                self.add_path('/Ac/L1/Energy/Forward', None, gettextcallback=_kwh)
                self.add_path('/Ac/L2/Energy/Forward', None, gettextcallback=_kwh)
                self.add_path('/Ac/L3/Energy/Forward', None, gettextcallback=_kwh)
                self.add_path('/Ac/L1/Energy/Reverse', None, gettextcallback=_kwh)
                self.add_path('/Ac/L2/Energy/Reverse', None, gettextcallback=_kwh)
                self.add_path('/Ac/L3/Energy/Reverse', None, gettextcallback=_kwh)
                self.add_path('/Ac/Energy/Forward', None, gettextcallback=_kwh) # energy bought from the grid
                self.add_path('/Ac/Energy/Reverse', None, gettextcallback=_kwh) # energy sold to the grid

    if physical == 'pvinverter':
        # if open() is ok, read register (modbus function 0x03)
        if modbusClient.is_socket_open():
            # read registers, store result in regs list
            regs = modbusClient.read_holding_registers(sunspecModels[1]["offset"], sunspecModels[1]["length"], unit=SERVER_UNIT)
            if regs.isError():
                log.error(f'regs.isError: {regs}')
                sys.exit()
            else:   
                self.add_path('/DeviceInstance', instance)
                self.add_path('/FirmwareVersion', _get_string(regs.registers[40:43]))
                self.add_path('/DataManagerVersion', VERSION)
                self.add_path('/Serial', _get_string(regs.registers[48:55]))
                self.add_path('/Mgmt/Connection', CONNECTION)
                self.add_path('/ProductId', 41284) # value used in ac_sensor_bridge.cpp of dbus-cgwacs
                self.add_path('/JPX', '+49 212 68828900')
                self.add_path('/ProductName', _get_string(regs.registers[0:15]) +" "+_get_string(regs.registers[16:31]))
                self.add_path('/Ac/Energy/Forward', None, gettextcallback=_kwh)
                self.add_path('/Ac/Power', None, gettextcallback=_w)
                self.add_path('/Ac/L1/Current', None, gettextcallback=_a)
                self.add_path('/Ac/L2/Current', None, gettextcallback=_a)
                self.add_path('/Ac/L3/Current', None, gettextcallback=_a)
                self.add_path('/Ac/Current', None, gettextcallback=_a)
                self.add_path('/Ac/L1/Energy/Forward', None, gettextcallback=_kwh)
                self.add_path('/Ac/L2/Energy/Forward', None, gettextcallback=_kwh)
                self.add_path('/Ac/L3/Energy/Forward', None, gettextcallback=_kwh)
                self.add_path('/Ac/L1/Power', None, gettextcallback=_w)
                self.add_path('/Ac/L2/Power', None, gettextcallback=_w)
                self.add_path('/Ac/L3/Power', None, gettextcallback=_w)
                self.add_path('/Ac/L1/Voltage', None, gettextcallback=_v)
                self.add_path('/Ac/L2/Voltage', None, gettextcallback=_v)
                self.add_path('/Ac/L3/Voltage', None, gettextcallback=_v)
                self.add_path('/Ac/MaxPower', M120_maxPower, gettextcallback=_w, onchangecallback=_maxpower_change, writeable=True)                
                self.add_path('/Ac/PowerLimit', M120_maxPower, gettextcallback=_w, onchangecallback=_powerlimit_change, writeable=True)
                self.add_path('/ErrorCode', None)
                self.add_path('/Position', config['DEFAULT']['Position'])
                self.add_path('/StatusCode', None)

    if physical == 'temp_pvinverter':
        # if open() is ok, read register (modbus function 0x03)
        if modbusClient.is_socket_open():
            # read registers, store result in regs list
            regs = modbusClient.read_holding_registers(sunspecModels[1]["offset"], sunspecModels[1]["length"], unit=SERVER_UNIT)
            if regs.isError():
                log.error(f'regs.isError: {regs}')
                sys.exit()
            else:   
                self.add_path('/DeviceInstance', instance)
                self.add_path('/FirmwareVersion', _get_string(regs.registers[40:43]))
                self.add_path('/DataManagerVersion', VERSION)
                self.add_path('/Serial', _get_string(regs.registers[48:55]))
                self.add_path('/Mgmt/Connection', CONNECTION)
                self.add_path('/ProductName', _get_string(regs.registers[0:15]) +" "+_get_string(regs.registers[16:31]))
                self.add_path('/ProductId', 0) 
                self.add_path('/CustomName', 'PV Inverter Temperature')
                self.add_path('/Temperature', None, gettextcallback=_c)
                self.add_path('/Status', 0)
                self.add_path('/TemperatureType', 0, writeable=True)

    if physical == 'limit_pvinverter':
        # if open() is ok, read register (modbus function 0x03)
        if modbusClient.is_socket_open():
            # read registers, store result in regs list
            regs = modbusClient.read_holding_registers(sunspecModels[1]["offset"], sunspecModels[1]["length"], unit=SERVER_UNIT)
            if regs.isError():
                log.error('regs.isError: ' + regs)
                sys.exit()
            else:   
                self.add_path('/DeviceInstance', instance)
                self.add_path('/FirmwareVersion', _get_string(regs.registers[40:43]))
                self.add_path('/DataManagerVersion', VERSION)
                self.add_path('/Serial', _get_string(regs.registers[48:55]))
                self.add_path('/Mgmt/Connection', CONNECTION)
                self.add_path('/ProductName', _get_string(regs.registers[0:15])+" "+_get_string(regs.registers[16:31]))
                self.add_path('/ProductId', 0) 
                self.add_path('/CustomName', 'PV Inverter Limiter active')
                self.add_path('/State', None)
                self.add_path('/Status', 0)
                self.add_path('/Type', 2, writeable=True)
                self.add_path('/Alarm', None, writeable=True)


    return self


# some globals 
baseRegister = 40000
M123_powerLimitScaleFactor = 1
M120_maxPower = 0
# Wert merken damit der ggf. per Timer neu gesetzt werden kann
PowerLimitPct = 0
refresh_timer = 0
globalConfig = 0


# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

# Setup logging
root = logging.getLogger()
root.setLevel(logging.INFO)
#root.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
#handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
root.addHandler(handler)

config = _getConfig()
SERVER_HOST = config['DEFAULT']['InverterIPAddress']
SERVER_PORT = config['DEFAULT']['InverterModbusPort']
SERVER_UNIT = int(config['DEFAULT']['InverterModbusUnit'])
CONNECTION  = "ModbusTCP " + SERVER_HOST + ":" + str(SERVER_PORT) + ", UNIT " + str(SERVER_UNIT)

log.info('Startup, trying connection to Modbus-Server: '+ CONNECTION)
modbusClient = ModbusClient(SERVER_HOST, port=SERVER_PORT ) 
modbusClient.auto_open=True

if not modbusClient.is_socket_open():
    if not modbusClient.connect():
        log.error("unable to connect to "+SERVER_HOST+":"+str(SERVER_PORT))
        sys.exit()

log.info('Connected to Modbus Server.')


log.info("Sunspec model query")
sunspecModels = _detect_sunspec_modules( baseRegister )
log.info("Found Sunspec models: " + json.dumps(sunspecModels))

dbusservice = {} # Dictonary to hold the multiple services
base = 'com.victronenergy'

# service defined by (base*, type*, id*, instance):
# * items are include in service name
# Create all the dbus-services we want
#dbusservice['grid']           = new_service(base, 'grid',           'grid',              0, 0)
dbusservice['pvinverter.pv0'] = new_service(base, 'pvinverter.pv0', 'pvinverter',        0, 20)
dbusservice['adc-temp0']      = new_service(base, 'temperature',    'temp_pvinverter',   0, 26)
#dbusservice['digitalinput0']  = new_service(base, 'digitalinput',    'limit_pvinverter', 0, 10)



# Everything done so just set a time to run an update function to update the data values every second.
log.info('Start update timer')
gobject.timeout_add(1000, _update)


log.info("Initial maxPowerData per Modbus auf 100% setzen")
try:
    pct = 100 * M123_powerLimitScaleFactor
    maxPowerData = [int(pct), 0, 300, 0, 1]
    log.debug(f'modbusClient.write_registers(sunspecModels[123][offset] + 3, {maxPowerData} , unit=SERVER_UNIT) to {maxPowerData}')
    modbusClient.write_registers(sunspecModels[123]['offset'] + 3, maxPowerData , unit=int(SERVER_UNIT))
except Exception as e:
    log.error('exception setting initial maxPowerData value to 100')
    log.exception(str(e))
    sys.exit()

log.info('Connected to dbus, everything set up, now switching over to GLib.MainLoop() (= event based)')
mainloop = gobject.MainLoop()
mainloop.run()
