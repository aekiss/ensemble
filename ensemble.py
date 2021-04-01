#!/usr/bin/env python
"""

Generate ensemble of ACCESS-OM2 experiments.

Latest version: https://github.com/aekiss/ensemble
Author: Andrew Kiss https://github.com/aekiss
Apache 2.0 License http://www.apache.org/licenses/LICENSE-2.0.txt
"""

from __future__ import print_function
import os
import shutil
import git
import numpy as np
import glob
import subprocess
try:
    import yaml
    import f90nml  # from https://f90nml.readthedocs.io/en/latest/
except ImportError:  # BUG: don't get this exception if payu module loaded, even if on python 2.6.6
    print('\nFatal error: modules not available.')
    print('On NCI, do the following and try again:')
    print('   module use /g/data/hh5/public/modules; module load conda/analysis3\n')
    raise

# ======================================================
# from https://gist.github.com/paulkernstock/6df1c7ad37fd71b1da3cb05e70b9f522
from yaml.representer import SafeRepresenter


class LiteralString(str):
    pass


def change_style(style, representer):
    def new_representer(dumper, data):
        scalar = representer(dumper, data)
        scalar.style = style
        return scalar
    return new_representer

represent_literal_str = change_style('|', SafeRepresenter.represent_str)
yaml.add_representer(LiteralString, represent_literal_str)
# ======================================================


def ensemble(yamlfile='ensemble.yaml', test=False):
    """
    Create and run an ensemble by varying only one parameter at a time.
    """
    # alternatively, could loop over all values of all parameters using `itertools.product`
    # see https://stackoverflow.com/questions/1280667/in-python-is-there-an-easier-way-to-write-6-nested-for-loops
    indata = yaml.load(open(yamlfile, 'r'), Loader=yaml.SafeLoader)
    template = indata['template']
    templatepath = os.path.join(os.getcwd(), template)
    templaterepo = git.Repo(templatepath)
    ensemble = []  # paths to ensemble members
    for fname, nmls in indata['namelists'].items():
        for group, names in nmls.items():
            for name, values in names.items():
                turningangle = [fname, group, name] == ['ice/cice_in.nml', 'dynamics_nml', 'turning_angle']
                for v in values:
                    exppath = os.path.join(os.getcwd(), '_'.join([template, name, str(v)]))
                    relexppath = os.path.relpath(exppath, os.getcwd())
                    expname = os.path.basename(relexppath)

                    if os.path.exists(exppath):
                        print('\n -- not creating', relexppath, '- already exists')
                        ensemble.append(exppath)
                        continue

                    # first check whether this set of parameters differs from template
                    with open(os.path.join(templatepath, fname)) as template_nml_file:
                        nml = f90nml.read(template_nml_file)
                        if turningangle:
                            cosw = np.cos(v * np.pi / 180.)
                            sinw = np.sin(v * np.pi / 180.)
                            skip = nml[group]['cosw'] == cosw \
                               and nml[group]['sinw'] == sinw
                        else:
                            skip = nml[group][name] == v
                    if skip:
                        print('\n -- not creating', relexppath, '- parameters are identical to', template)
                        continue

                    print('\ncreating', relexppath)

                    # clone template, fix up git remotes, set up new branch
                    exprepo = templaterepo.clone(exppath)
                    exprepo.remotes.origin.rename('template')
                    exprepo.create_remote('origin', templaterepo.remotes.origin.url)
# TODO: first checkout commit corresponding to restart?
                    exprepo.git.checkout('HEAD', b=expname)  # switch to a new branch

                    # perturb parameters
                    fpath = os.path.join(exppath, fname)
                    if turningangle:
                        f90nml.patch(fpath, {group: {'cosw': cosw}}, fpath+'_tmp2')
                        f90nml.patch(fpath+'_tmp2', {group: {'sinw': sinw}}, fpath+'_tmp')
                        os.remove(fpath+'_tmp2')
                    else:  # general case
                        f90nml.patch(fpath, {group: {name: v}}, fpath+'_tmp')
                    os.rename(fpath+'_tmp', fpath)
                    if not exprepo.is_dirty():  # additional check in case of match after roundoff
                        print(' *** deleting', relexppath, '- parameters are identical to', template)
                        shutil.rmtree(exppath)
                        continue

                    # set SYNCDIR in sync_data.sh
                    sdpath = os.path.join(exppath, 'sync_data.sh')
                    with open(sdpath+'_tmp', 'w') as wf:
                        with open(sdpath, 'r') as rf:
                            for line in rf:
                                if line.startswith('SYNCDIR='):
                                    syncbase = os.path.dirname(line[len('SYNCDIR='):])
                                    syncdir = os.path.join(syncbase, expname)
                                    wf.write('SYNCDIR='+syncdir+'\n')
                                else:
                                    wf.write(line)
                    os.rename(sdpath+'_tmp', sdpath)
                    if os.path.exists(syncdir):
                        print(' *** deleting', relexppath, '- SYNCDIR', syncdir, 'already exists')
                        shutil.rmtree(exppath)
                        continue

                    if indata['startfrom'] != 'rest':

                        # create archive symlink
                        if not test:
                            subprocess.run('cd ' + exppath + ' && payu sweep && payu setup', check=False, shell=True)
                            workpath = os.path.realpath(os.path.join(exppath, 'work'))
                            subprocess.run('cd ' + exppath + ' && payu sweep', check=True, shell=True)
                        else:  # simulate effect of payu setup (for testing without payu)
                            workpath = os.path.realpath(os.path.join('test', 'work', expname))
                            os.makedirs(workpath)
                            os.symlink(workpath, os.path.join(exppath, 'work'))
                            archivepath = workpath.replace('/work/', '/archive/')
                            os.makedirs(archivepath)
                            workpath = os.path.realpath(os.path.join(exppath, 'work'))
                            os.remove(os.path.join(exppath, 'work'))
                            shutil.rmtree(workpath)
                            # also make template restart symlink if it doesn't exist
                            if template == 'test/1deg_jra55_iaf':  # e.g. testing fresh clone
                                templatearchive = os.path.join(templatepath, 'archive')
                                if not os.path.exists(templatearchive):
                                    os.symlink(archivepath.replace(expname, os.path.basename(template)), templatearchive)
                        # payu setup creates archive dir but not symlink,
                        # so infer archive path from work dest and link to it
                        archivepath = workpath.replace('/work/', '/archive/')
                        if glob.glob(os.path.join(archivepath, 'output*')) +\
                           glob.glob(os.path.join(archivepath, 'restart*')):
                            print(' *** deleting', relexppath, '- archive', archivepath, 'already contains restarts and/or outputs')
                            shutil.rmtree(exppath)
                            continue
                        os.symlink(archivepath, os.path.join(exppath, 'archive'))

                        # symlink restart initial conditions
                        d = os.path.join('archive', 'restart'+str(indata['startfrom']))
                        restartpath = os.path.realpath(os.path.join(template, d))
                        os.symlink(restartpath, os.path.join(exppath, d))

                        # copy template/output[startfrom]/ice/cice_in.nml
                        d = os.path.join('archive', 'output'+str(indata['startfrom']), 'ice')
                        os.makedirs(os.path.join(exppath, d))
                        shutil.copy(os.path.join(template, d, 'cice_in.nml'),
                                    os.path.join(exppath, d))

                    # set jobname in config.yaml to reflect experiment
                    # don't use yaml package as it doesn't preserve comments
                    configpath = os.path.join(exppath, 'config.yaml')
                    with open(configpath+'_tmp', 'w') as wf:
                        with open(configpath, 'r') as rf:
                            for line in rf:
                                if line.startswith('jobname:'):
                                    wf.write('jobname: '+'_'.join([name, str(v)])+'\n')
                                else:
                                    wf.write(line)
                    os.rename(configpath+'_tmp', configpath)

                    # update metadata.yaml
                    metadata = yaml.load(open(os.path.join(exppath, 'metadata.yaml'), 'r'), Loader=yaml.SafeLoader)
                    desc = metadata['description']
                    desc += '\nNOTE: this is a perturbation experiment, but the description above is for the control run.'
                    desc += '\nThis perturbation experiment is based on the control run ' + templatepath
                    if indata['startfrom'] == 'rest':
                        desc += '\nbut with condition of rest'
                    else:
                        desc += '\nbut with initial condition ' + restartpath
                    if turningangle:
                        desc += '\nand ' + ' -> '.join([fname, group, 'cosw and sinw']) +\
                            ' changed to give a turning angle of ' + str(v) + ' degrees.'
                    else:
                        desc += '\nand ' + ' -> '.join([fname, group, name]) +\
                            ' changed to ' + str(v)
                    metadata['description'] = LiteralString(desc)
                    metadata['notes'] = LiteralString(metadata['notes'])
                    metadata['keywords'] += ['perturbation', name]
                    if turningangle:
                        metadata['keywords'] += ['cosw', 'sinw']
                    with open(os.path.join(exppath, 'metadata.yaml'), 'w') as f:
                        yaml.dump(metadata, f, default_flow_style=False, sort_keys=False)

                    # remove run_summary_*.csv
                    for f in glob.glob(os.path.join(exppath, 'run_summary_*.csv')):
                        exprepo.git.rm(os.path.basename(f))

                    # commit
                    exprepo.git.commit(a=True, m='set up '+expname)

                    ensemble.append(exppath)

# count existing runs and do additional runs if needed
    if indata['nruns'] > 0:
        for exppath in ensemble:
            doneruns = len(glob.glob(os.path.join(exppath, 'archive', 'output[0-9][0-9][0-9]*'))) - 1
            newruns = indata['nruns'] - doneruns
            if newruns > 0:
#                cmd = 'cd ' + exppath + ' && payu sweep && payu run -n ' + str(newruns)
                cmd = 'cd ' + exppath + ' && payu run -n ' + str(newruns)
                if test:
                    cmd = '# ' + cmd
                print('\n'+cmd)
                subprocess.run(cmd, check=False, shell=True)
            else:
                print('\n --', exppath, 'has already completed', doneruns, 'runs')
    print()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=
        'Generate ensemble of ACCESS-OM2 experiments.\
        Latest version and help: https://github.com/aekiss/ensemble')
    parser.add_argument('yamlfile', metavar='yamlfile', type=str, nargs='?',
                        default='ensemble.yaml',
                        help='YAML file specifying parameter values to use for ensemble; default is ensemble.yaml')
    parser.add_argument('--test',
                        action='store_true', default=False,
                        help='for testing a fresh clone, with no payu dependency')
    args = parser.parse_args()
    yamlfile = vars(args)['yamlfile']
    test = vars(args)['test']
ensemble(yamlfile, test=test)
