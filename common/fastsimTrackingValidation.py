from CRABAPI.RawCommand import crabCommand

import cmsDriverAPI, pickle, os, time, sys
import fastsimTrackingHelpers as helper

from collections import OrderedDict

#----------------------Maker Classes----------------------------------#
class Maker(object):
    """Parent class of processing objects"""
    def __init__(self, stepname, prev,options): # name is either AOD, MINIAOD, NANOAOD, TRACKVAL, BTAGVAL, ANALYSIS
        super(Maker, self).__init__()

        # Store reference to maker this is reliant on ("False" for AOD which is the top level)
        self.stepname = stepname
        self.tag = options.tag
        self.prev = prev
        if self.prev == None:
            raise TypeError('The previous object needs to be made before running this step. If the current step already exists, please make sure it is loading properly.')

        # Basic initializing
        if self.stepname in ['AOD','BTAGVAL','MINIAOD','NANOAOD'] and options.crab:
            self.crab = True
        else:
            self.crab = False
        self.localsavedir = '%s/'%self.stepname
        self.picklename = '%s%s_%s.p'%(self.localsavedir, self.stepname,self.tag)
        self.exists = self.checkExists() # check if pickle already exists

        # Skip checkDone?
        self.skipCheckDone = options.bypassChecks

        # Get input files (output of previous step)
        if self.prev != False:
            
            self.input_file = '%sFastSim_%s_%s.root'%(self.prev.localsavedir, self.prev.stepname,self.tag if self.stepname not in ['BTAGVAL','TRACKVAL'] else self.tag+'_inDQM')
            # If crab, hadd together locally from EOS
            if self.prev.crab:
                self.crabWait()
                if not os.path.exists(self.input_file):
                    # if os.path.getmtime(self.input_file) < os.path.getmtime(self.localsavedir+'crab_submit.json'):
                    helper.haddFromEOS(self.prev.stepname,self.prev.eosPath)
        else:
            self.input_file = ''

        # Crab configuration if needed
        if self.crab:
            # Get crab setup to track
            self.crab_config = helper.MakeCrabConfig(stepname, options.tag, files=[self.input_file], storageSite=options.storageSite,nevents=options.nevents,dataset=options.cfi) # make crab config
            self.crabDir = self.localsavedir+'crab_'+self.crab_config.General.requestName # record crab task dir name
            self.cmsRun_file = self.crab_config.JobType.psetName

            # Set after submit
            self.submit_out = None
            self.eosPath = None
        # Still setup cmsRun file
        else:
            self.cmsRun_file = '%s/FastSimValidation_%s_%s.py'%(stepname,stepname,self.tag)

    # Save out obj to pickle
    def save(self):
        pickle.dump(self, open(self.picklename, "wb"))
        if self.crab:
            helper.SaveToJson('%scrab_submit.json'%self.localsavedir, self.submit_out)
            # helper.SaveToJson('%scrab_status.json'%self.localsavedir, crabCommand('status',dir = self.crabDir))

    # Check if pickled obj already exists
    def checkExists(self):
        if self.picklename == '':
            raise ValueError('Pickle name is empty. Cannot check for existence.')
        exists = os.path.exists(self.picklename)
        if exists:
            print('WARNING: %s has already been made. Will overwrite.'%self.picklename)
        
        return exists

    # Check if self crab job finished
    def checkDone(self):
        # Send status output to a file so we dont have to print it several times
        with helper.redirection(os.devnull):
            status = crabCommand('status',dir = self.crabDir)

        if status['status'] == 'COMPLETED':
            print ('Job %s COMPLETED'%self.crabDir)
            done = True
        elif status['status'] == 'FAILED':
            raise Exception('Job %s has FAILED. Please attempt to fix the issue manually before attempting to rerun.'%self.crabDir)
        else:
            done = False

        return done,status

    # Wait for crab job that self relies on to finish
    def crabWait(self):
        if self.prev != False and not self.skipCheckDone:
            doneBool,crabInfo = self.prev.checkDone()
            while not doneBool:
                stati = crabInfo['jobsPerStatus']
                print_out = '\t'.join(['%s: %s' %(k, stati[k]) for k in stati.keys()])
                sys.stdout.write(print_out+'\r')
                time.sleep(60)
                sys.stdout.flush()
                doneBool,crabInfo = self.prev.checkDone()

    def run_gen(self):
        print (self.cmsDriver_args)
        helper.MakeRunConfig(self.cmsDriver_args)
        if not os.path.exists(self.cmsRun_file):
            raise Exception('%s was not created.'%self.cmsRun_file)   
        # Run crab  
        if self.crab:   
            print (self.crab_config)
            self.submit_out = crabCommand('submit',config=self.crab_config)
            self.setEOSdir()
        # Don't run crab
        else:
            helper.executeCmd('cmsRun '+self.cmsRun_file)

    def setEOSdir(self):
        full_request = self.submit_out['uniquerequestname']
        # first_underscore = full_request.find('_')+1
        # request_time_data = full_request[:full_request.find('_',first_underscore)]
        request_time_data = full_request.split(':')[0]
        # /store/user/lcorcodi/FastSimValidation/TTbar_13TeV_TuneCUETP8M1/test/200819_192031/0000
        self.eosPath = '%s/%s/%s/%s/0000/'%(self.crab_config.Data.outLFNDirBase, self.crab_config.Data.outputPrimaryDataset, self.crab_config.Data.outputDatasetTag, request_time_data)#, self.crab_config.JobType.psetName)
        return self.eosPath

    def _addExtraOptions(self,options,step=None):
        if step == 'AOD': extra_option_types = ["datamix","procModifiers","pileup_input","geometry","pileup"]
        else: extra_option_types = []
        if options.cmsDriver != '':
            for o in options.cmsDriver.split('--'):
                if o.strip() == '': continue
                self.cmsDriver_args.append('--'+o.strip())
        for o in extra_option_types:
            if hasattr(options,o):
                self.cmsDriver_args.append('--'+o.strip()+'='+getattr(options,o))
                if 'datamix' in o: # Need to add DATAMIX before L1 is using pileup
                    for i,a in enumerate(self.cmsDriver_args):
                        if '-s ' in a:
                            insert_idx = a.find('L1,')
                            self.cmsDriver_args[i] = a[:insert_idx]+'DATAMIX,'+a[insert_idx:]

# Fastsim AOD production
class MakeAOD(Maker):
    """docstring for MakeAOD"""
    def __init__(self,options):
        super(MakeAOD, self).__init__('AOD',False,options)
        
        self.cmsDriver_args = [
            '--evt_type '+options.cfi, '--mc',
            '--conditions '+options.conditions, '--scenario pp',
            '--fast', '-n '+options.nevents, '--nThreads 1',
            '--era '+options.era, '--beamspot '+options.beamspot,
            '--datatier AODSIM,DQMIO', '--eventcontent AODSIM,DQM',
            # '-s GEN,SIM,RECOBEFMIX,DIGI:pdigi_valid,L1,DIGI2RAW,L1Reco,RECO,VALIDATION:@standardValidation,DQM:@standardDQM', 
            '--python_filename '+self.cmsRun_file, '--fileout %sFastSim_AOD_%s.root'%(self.localsavedir if not self.crab else '',self.tag)
        ]

        if 'Configuration' in options.cfi:
            with open('../'+options.cfi,'r') as cfi:
                runLHE = False
                for l in cfi.readlines():
                    if 'ExternalLHEProducer' in l:
                        runLHE = True
                        break
        else:
            runLHE = False
            
        if not runLHE:
            self.cmsDriver_args.append('-s GEN,SIM,RECOBEFMIX,DIGI:pdigi_valid,L1,DIGI2RAW,L1Reco,RECO,VALIDATION:@standardValidation,DQM:@standardDQM')
        else:
            self.cmsDriver_args.append('-s LHE,GEN,SIM,RECOBEFMIX,DIGI:pdigi_valid,L1,DIGI2RAW,L1Reco,RECO,VALIDATION:@standardValidation,DQM:@standardDQM')

        self._addExtraOptions(options,step='AOD')

    def run(self):
        self.run_gen()

#### Btag validation
class MakeBtagVal(Maker):
    """docstring for MakeBtagVal"""
    def __init__(self, MiniAODobj,options):
        super(MakeBtagVal, self).__init__('BTAGVAL',MiniAODobj,options)

        self.cmsDriver_args = [
            '--filein file:%s'%(self.input_file), '--mc', 
            '--conditions '+options.conditions, '--scenario pp',
            '--fast', '-n '+options.nevents, '--nThreads 1',
            '--era '+options.era, '--beamspot '+options.beamspot,
            '--filetype DQM',
            '-s HARVESTING:validationHarvesting',
            '--python_filename '+self.cmsRun_file # no --fileout, see `mv` command below
        ]
        self._addExtraOptions(options)

    def run(self):
        self.run_gen()
        helper.executeCmd('mv DQM_V0001_R000000001__Global__CMSSW_X_Y_Z__RECO.root %sharvest_%s.root'%(self.localsavedir if not self.crab else '',self.tag))

#### Track validation 
class MakeTrackVal(Maker):
    """docstring for MakeTrackVal"""
    def __init__(self, AODobj,options):
        super(MakeTrackVal, self).__init__('TRACKVAL',AODobj,options)

        self.crab = False
        self.cmsDriver_args = [
            '--filein file:%s'%(self.input_file), '--mc',
            '--conditions '+options.conditions, '--scenario pp',
            '-n -1', '--nThreads 1',
            '--era '+options.era,
            '-s HARVESTING:@trackingOnlyValidation+@trackingOnlyDQM+postProcessorHLTtrackingSequence',
            '--python_filename '+self.cmsRun_file,
            '--filetype DQM'#, '--fileout %s
        ]

        if options.customise != '':
            self.cmsDriver_args.append('--customise '+options.customise)
        
    def run(self):
        if self.prev.crab: self.crabWait()
        self.run_gen()
        helper.executeCmd('mv DQM_V0001_R000000001__Global__CMSSW_X_Y_Z__RECO.root %sFastSim_harvest_%s.root'%(self.localsavedir if not self.crab else '',self.tag))

## MiniAOD production
class MakeMiniAOD(Maker):
    """docstring for MakeMiniAOD"""
    def __init__(self, AODobj,options):
        super(MakeMiniAOD, self).__init__('MINIAOD',AODobj,options)

        self.cmsDriver_args = [
            '--filein file:%s'%(self.input_file), '--mc',
            '--conditions '+options.conditions, '--scenario pp',
            '--fast', '-n '+options.nevents, '--nThreads 1',
            '--era '+options.era, '--beamspot '+options.beamspot,
            '--datatier MINIAODSIM', '--eventcontent MINIAODSIM',
            '-s PAT', 
            '--python_filename '+self.cmsRun_file, '--fileout %sFastSim_MINIAOD_%s.root'%(self.localsavedir if not self.crab else '',self.tag), '--runUnscheduled']
        self._addExtraOptions(options)

    def run(self):
        self.run_gen()

## NanoAOD production
class MakeNanoAOD(Maker):
    """docstring for MakeNanoAOD"""
    def __init__(self, MiniAODobj,options):
        super(MakeNanoAOD, self).__init__('NANOAOD',MiniAODobj,options)

        self.cmsDriver_args = [
            '--filein file:%s'%(self.input_file), '--mc',
            '--conditions '+options.conditions, '--scenario pp',
            '--fast', '-n '+options.nevents, '--nThreads 1',
            '--era '+options.era+',run2_nanoAOD_102Xv1', '--beamspot '+options.beamspot,
            '--datatier NANOAODSIM', '--eventcontent NANOAODSIM',
            '-s NANO', 
            '--python_filename '+self.cmsRun_file, '--fileout %sFastSim_NANOAOD_%s.root'%(self.localsavedir if not self.crab else '',self.tag), 
            '''--customise_commands="process.add_(cms.Service('InitRootHandlers',EnableIMT=cms.untracked.bool(False)))"'''
        ]
        self._addExtraOptions(options)

    def run(self):
        self.run_gen()
