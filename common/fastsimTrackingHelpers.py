try:
import CRABClient
except ImportError as e:
    print('Cannot import CRABClient. Please try sourcing crab-setup (in bash: `source /cvmfs/cms.cern.ch/common/crab-setup.sh`)')
    raise e
from CRABClient.UserUtilities import config
import cmsDriverAPI, pickle, os, json

import json, subprocess
from contextlib import contextmanager
from collections import OrderedDict
from fastsimTrackingValidation import *

#------------------JSON config reading-------------------------------#
# Function stolen from https://stackoverflow.com/questions/9590382/forcing-python-json-module-to-work-with-ascii
def openJSON(f):
    with open(f) as fInput_config:
        input_config = json.load(fInput_config, object_hook=ascii_encode_dict)  # Converts most of the unicode to ascii
    return input_config

def ascii_encode_dict(data):    
    ascii_encode = lambda x: x.encode('ascii') if isinstance(x, unicode) else x 
    return dict(map(ascii_encode, pair) for pair in data.items())

def handleOptions(optparser):
    (options,args) = optparser.parse_args()# establish main option object

    if options.config != '': # if there's a config...
        config = openJSON(options.config)
        for k in config.keys():
            # ... set config option if it exists and only overwriting CL default
            if k in optparser.defaults.keys() and (getattr(options,k) == optparser.defaults[k]):
                setattr(options,k,config[k])

    printOptions(options)
    return options

def printOptions(options):
    print('Will use options: ')
    d_options = vars(options)
    for k in d_options.keys():
        print ('\t%s = %s'%(k,d_options[k]))

#--------------------Helpers-----------------------------------------#
def ParseSteps(steps):
    step_list = steps.split(',')
    if 'ALL' in step_list: 
        allBool = True
    else:
        allBool = False

    all_steps = ['AOD','TRACKVAL','BTAGVAL','MINIAOD','NANOAOD','ANALYSIS']
    out = OrderedDict()

    if allBool:
        for s in all_steps:
            out[s] = True
    else:
        for step in all_steps:
            if step in step_list:
                out[step] = True
            else:
                out[step] = False

    print ('Will process %s'%[s for s in out.keys() if out[s] == True])
    return out

def GetWorkingArea(cmssw,testdir,step_bools):
    working_location = testdir+'/'+cmssw+'/src'

    if os.path.isdir(working_location):
        working_location+='/FastSimValWorkspace'
        if not os.path.isdir(working_location):
            print('mkdir '+working_location)
            os.mkdir(working_location)
        for f in ['AOD','TRACKVAL','BTAGVAL','MINIAOD','NANOAOD','ANALYSIS']:
            if not os.path.isdir(working_location+'/'+f) and step_bools[f]:
                print ('mkdir '+working_location+'/'+f)
                os.mkdir(working_location+'/'+f)
    else:
        raise NameError('%s does not exist'%working_location)
    
    return working_location

def GetMakers(step_bools,options):
    makers = OrderedDict()
    
    for step_name in step_bools.keys():
        if step_bools[step_name]:
            if step_name == 'AOD': # AOD - no reliance
                makers[step_name] = MakeAOD(options)
            elif step_name == 'TRACKVAL': # TRACKVAL - relies on AOD/DQM
                makers[step_name] = MakeTrackVal(makers['AOD'],options)
            elif step_name == 'BTAGVAL': # BTAGVAL - relies on AOD/DQM
                makers[step_name] = MakeBtagVal(makers['AOD'],options)
            elif step_name == 'MINIAOD': # MINIAOD - relies on AOD
                makers[step_name] = MakeMiniAOD(makers['AOD'],options)
            elif step_name == 'NANOAOD': # NANOAOD - relies on MINIAOD
                makers[step_name] = MakeNanoAOD(makers['MINIAOD'],options)
            elif step_name == 'ANALYSIS': # ANALYSIS - relies on NANOAOD
                makers[step_name] = MakeAnalysis(makers['NANOAOD'],options)
        else:
            # Returns None and warning message if it doesn't exist
            makers[step_name] = LoadMaker('{0}/{0}.p'.format(step_name))
 
    return makers

def LoadMaker(name):
    if os.path.exists(name):
        return pickle.load(open(name, "rb"))
    else:
        print ('WARNING: Cannot find %s. Further steps may not run.'%name)
        return None

def SaveToJson(path,outdict):
    out = open(path,'w')
    out.write(json.dumps(outdict))
    out.close()

@contextmanager
def cd(newdir):
    print ('cd '+newdir)
    prevdir = os.getcwd()
    if not os.path.isdir(newdir):
        os.mkdir(newdir)
    os.chdir(os.path.expanduser(newdir))
    try:
        yield
    finally:
        os.chdir(prevdir)

def executeCmd(cmd,bkg=False):
    print (cmd)
    if bkg:
        subprocess.Popen(cmd.split(' '))
    else:
        subprocess.call([cmd],shell=True,executable='/bin/bash')

def eosls(path,withxrd=True,cs=False): #cs = comma-separated
    xrd = 'root://cmsxrootd.fnal.gov'
    cmd = 'xrdfs %s ls %s'%(xrd,path)
    file_list = subprocess.check_output(cmd.split(' ')).split('\n')
    while '' in file_list:
        file_list.remove('')
    # Prepend xrd
    if withxrd:
        out = []
        for f in file_list:
            out.append(xrd+'/'+f)
    # or not
    else:
        out = file_list
    # Make a comma separated string instead of list
    if cs:
        out = ','.join(out)
    return out

def haddFromEOS(stepname, eospath):
    # eosfiles = eosls(eospath,withxrd=True,cs=True)
    xrd = 'root://cmsxrootd.fnal.gov'
    # Will skip inDQM
    grep = "grep 'FastSim_%s_[0-9]*\.root"%(stepname)
    executeCmd("hadd -f %s/FastSim_%s.root `xrdfs %s ls -u %s | %s`"%(stepname,stepname,xrd,eospath,grep))
    # Now do inDQM
    if stepname == 'AOD':
        grep = "grep 'FastSim_%s_[0-9]*\.root"%(stepname+'_inDQM')
        executeCmd("hadd -f %s/FastSim_%s.root `xrdfs %s ls -u %s | %s`"%(stepname,stepname+'_inDQM',xrd,eospath,grep))

#------------CMS interfacing-----------------------------------------#
def MakeCrabConfig(stepname, tag, files=[],storageSite='T3_US_FNALLPC',outLFNdir='/store/user/%s/FastSimValidation/'%os.environ['USER'],nevents=None,dataset=''):
    # As of now, only first statement will run (but keeping functionality)
    if stepname in ['AOD']:#,'BTAGVAL','MINIAOD','NANOAOD']:
        gen = True
        ana = False
    else:
        gen = False
        ana = True

    if files[0] == '' and gen and stepname != 'AOD':
        raise ValueError("You are trying to generate but the inputFiles list is non-empty. Perhaps you meant to run an analysis script?")
    if files == [] and ana:
        raise ValueError("You are trying to analyze but the inputFiles list is empty.")

    crab_config = config()
    crab_config.General.requestName = 'FastSimValidation_%s'%stepname
    crab_config.General.workArea = stepname
    crab_config.General.transferOutputs = True
    crab_config.JobType.psetName = '%s/FastSimValidation_%s.py'%(stepname,stepname)
    crab_config.Data.publication = False
    if stepname == 'AOD':
        crab_config.Data.totalUnits = int(nevents)
        # crab_config.Data.inputDataset = dataset
    crab_config.Site.storageSite = storageSite
    crab_config.Data.outLFNDirBase = outLFNdir
    crab_config.Data.outputPrimaryDataset = dataset.replace('_cfi','')
    crab_config.Data.outputDatasetTag = tag
    if files != ['']:
        crab_config.Data.inputFiles = files

    # Again, first statement will always be the one to execute
    if gen:
        crab_config.JobType.pluginName = 'PrivateMC'
        crab_config.Data.splitting = 'EventBased'
        crab_config.Data.unitsPerJob = 1000
    elif ana:
        crab_config.JobType.pluginName = 'Analysis'
        crab_config.Data.splitting = 'FileBased'
        crab_config.Data.unitsPerJob = 1

    return crab_config

def MakeRunConfig(inputArgs):
    final_string = ''
    for a in inputArgs:
        final_string += a+' '
    final_string += '--no_exec'
    print final_string.split()
    cmsDriverAPI.run(final_string.split())
    # remove_spaces = []
    # for a in inputArgs:
    #     s = a.split(' ')
    #     for p in s:
    #         if p != '': remove_spaces.append(p)
    # driver_args = remove_spaces
    # driver_args.append('--no_exec')
    # print (driver_args)
    # cmsDriverAPI.run(driver_args)
