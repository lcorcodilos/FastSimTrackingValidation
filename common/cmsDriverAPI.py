#! /usr/bin/env python

# A Pyrelval Wrapper
# Adapted from cmsDriver.py by Lucas Corcodilos to be accessible from within a python script
# added arguments to run() and changed options parsing to OptionsFromItems()

from __future__ import print_function
def run(argList):
        import sys
        import os
        import Configuration.Applications
        from Configuration.Applications.ConfigBuilder import ConfigBuilder
        from Configuration.Applications.cmsDriverOptions import OptionsFromItems#CommandLine
        options = OptionsFromItems(argList)
        options.arguments = reduce(lambda x, y: x+' '+y, argList)

        # after cleanup of all config parameters pass it to the ConfigBuilder
        configBuilder = ConfigBuilder(options, with_output = True, with_input = True)

        # Switch on any eras that have been specified. This is not required to create
        # the file, it is only relevant if dump_python is set. It does have to be done
        # before the prepare() call though. If not, then the config files will be loaded
        # without applying the era changes. This doesn't affect the config file written,
        # but when the dump_python branch uses execfile to read it back in it doesn't
        # reload the modules - it picks up a reference to the already loaded ones. 
        if hasattr( options, "era" ) and options.era is not None :
            from Configuration.StandardSequences.Eras import eras
            for eraName in options.era.split(',') :
                getattr( eras, eraName )._setChosen()
        
        configBuilder.prepare()
        # fetch the results and write it to file
        config = file(options.python_filename,"w")
        config.write(configBuilder.pythonCfgCode)
        config.close()

        # handle different dump options
        if options.dump_python:
            result = {}
            execfile(options.python_filename, result)
            process = result["process"]
            expanded = process.dumpPython()
            expandedFile = file(options.python_filename,"w")
            expandedFile.write(expanded)
            expandedFile.close()
            print("Expanded config file", options.python_filename, "created")
            sys.exit(0)           
  
        if options.no_exec_flag:
            print("Config file "+options.python_filename+ " created")
            # sys.exit(0)
        else:
            commandString = options.prefix+" cmsRun "+options.suffix
            print("Starting "+commandString+' '+options.python_filename)
            commands = commandString.lstrip().split()
            os.execvpe(commands[0],commands+[options.python_filename],os.environ)
            # sys.exit()
        return None
   
