#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""Calibration by NSGA-II algorithm.
    @author   : Liangjun Zhu
    @changelog: 18-01-22  lj - initial implementation.\n
                18-02-09  lj - compatible with Python3.\n
                18-07-10  lj - Support MPI version of SEIMS.\n
                18-08-26  lj - Gather the execute time of all model runs. Plot pareto graphs.\n
                18-08-29  jz,lj,sf - Add Nutrient calibration step.
"""
from __future__ import absolute_import, division

import array
import os
import random
import time
import sys

if os.path.abspath(os.path.join(sys.path[0], '..')) not in sys.path:
    sys.path.insert(0, os.path.abspath(os.path.join(sys.path[0], '..')))

import numpy
from deap import base
from deap import creator
from deap import tools
from deap.benchmarks.tools import hypervolume
from copy import deepcopy
from pygeoc.utils import UtilClass

from scenario_analysis.utility import print_message
from scenario_analysis.userdef import initIterateWithCfg, initRepeatWithCfg
from scenario_analysis.visualization import plot_pareto_front, plot_hypervolume_single
from calibration.config import CaliConfig, get_cali_config
from run_seims import MainSEIMS

from calibration.calibrate import Calibration, initialize_calibrations, calibration_objectives
from calibration.calibrate import TimeseriesData, ObsSimData
from calibration.userdef import write_param_values_to_mongodb, output_population_details

# Definitions, assignments, operations, etc. that will be executed by each worker
#    when paralleled by SCOOP.
# Thus, DEAP related operations (initialize, register, etc.) are better defined here.

object_vars = list()
object_names = ['NSE', 'RSR', 'PBIAS']
# step can be one of 'Q', 'SED', 'NUTRIENT'.
step = 'Q'
filter_NSE = False  # Filter scenarios which NSE less than 0 for the next generation
# Multiobjects definition:
if step == 'Q':
    # Step 1: Calibrate discharge, max. Nash-Sutcliffe, min. RSR, and min. |PBIAS| (percent)
    object_vars = ['Q']
    multi_weight = (2., -1., -1.)  # NSE taken bigger weight (actually used)
    worse_objects = [-1., 100., 10.]
elif step == 'SED':
    # Step 2: Calibration sediment, max. NSE-SED, min. RSR-SED, min. |PBIAS|-SED, and max. NSE-Q
    object_vars = ['SED', 'Q']
    multi_weight = (2., -1., -1., 1.)  # NSE of sediment taken a bigger weight
    worse_objects = [-100., 100., 100., -100.]
elif step == 'NUTRIENT':
    # Step 3: Calibration NSE-TN, NSE-TP, NSE-Q, NSE-SED
    # Or, NSE-TN, RSR-TN, |PBIAS|-TN, NSE-TP, RSR-TP, |PBIAS|-TP, NSE-Q, NSE-SED
    object_vars = ['CH_TN', 'CH_TP', 'Q', 'SED']
    multi_weight = (1., 1., 1., 1.)
    worse_objects = [-100., -100., -100., -100.]
else:
    print('The step of calibration should be one of [Q, SED, NUTRIENT]!')
    exit(0)

creator.create('FitnessMulti', base.Fitness, weights=multi_weight)
# The FitnessMulti class equals to (as an example):
# class FitnessMulti(base.Fitness):
#     weights = (2., -1., -1.)
creator.create('Individual', array.array, typecode='d', fitness=creator.FitnessMulti,
               gen=-1, id=-1,
               obs=TimeseriesData, sim=TimeseriesData,
               cali=ObsSimData, vali=ObsSimData,
               io_time=0., comp_time=0., simu_time=0., runtime=0.)
# The Individual class equals to:
# class Individual(array.array):
#     gen = -1  # Generation No.
#     id = -1   # Calibration index of current generation
#     def __init__(self):
#         self.fitness = FitnessMulti()

# Register NSGA-II related operations
toolbox = base.Toolbox()
toolbox.register('gene_values', initialize_calibrations)
toolbox.register('individual', initIterateWithCfg, creator.Individual, toolbox.gene_values)
toolbox.register('population', initRepeatWithCfg, list, toolbox.individual)
toolbox.register('evaluate', calibration_objectives)

# mate and mutate
toolbox.register('mate', tools.cxSimulatedBinaryBounded)
toolbox.register('mutate', tools.mutPolynomialBounded)

toolbox.register('select', tools.selNSGA2)


def main(cfg):
    """Main workflow of NSGA-II based Scenario analysis."""
    random.seed()
    print_message('Population: %d, Generation: %d' % (cfg.opt.npop, cfg.opt.ngens))

    # Initial timespan variables
    stime = time.time()
    plot_time = 0.
    allmodels_exect = list()  # execute time of all model runs

    # create reference point for hypervolume
    ref_pt = numpy.array(worse_objects) * multi_weight * -1

    stats = tools.Statistics(lambda sind: sind.fitness.values)
    stats.register('min', numpy.min, axis=0)
    stats.register('max', numpy.max, axis=0)
    stats.register('avg', numpy.mean, axis=0)
    stats.register('std', numpy.std, axis=0)
    logbook = tools.Logbook()
    logbook.header = 'gen', 'evals', 'min', 'max', 'avg', 'std'

    # read observation data from MongoDB
    cali_obj = Calibration(cfg)

    # Read observation data just once
    model_cfg_dict = cali_obj.model.ConfigDict
    model_obj = MainSEIMS(args_dict=model_cfg_dict)
    obs_vars, obs_data_dict = model_obj.ReadOutletObservations(object_vars)

    # Initialize population
    param_values = cali_obj.initialize(cfg.opt.npop)
    pop = list()
    for i in range(cfg.opt.npop):
        ind = creator.Individual(param_values[i])
        ind.gen = 0
        ind.id = i
        ind.obs.vars = obs_vars[:]
        ind.obs.data = deepcopy(obs_data_dict)
        pop.append(ind)
    param_values = numpy.array(param_values)

    # Write calibrated values to MongoDB
    # TODO, extract this function, which is same with `Sensitivity::write_param_values_to_mongodb`.
    write_param_values_to_mongodb(cfg.model.host, cfg.model.port, cfg.model.db_name,
                                  cali_obj.ParamDefs, param_values)
    # get the low and up bound of calibrated parameters
    bounds = numpy.array(cali_obj.ParamDefs['bounds'])
    low = bounds[:, 0]
    up = bounds[:, 1]
    low = low.tolist()
    up = up.tolist()
    pop_select_num = int(cfg.opt.npop * cfg.opt.rsel)
    init_time = time.time() - stime

    def evaluate_parallel(invalid_pops):
        """Evaluate model by SCOOP or map, and set fitness of individuals
         according to calibration step."""
        popnum = len(invalid_pops)
        labels = list()
        try:  # parallel on multi-processors or clusters using SCOOP
            from scoop import futures
            invalid_pops = list(futures.map(toolbox.evaluate, [cali_obj] * popnum, invalid_pops))
        except ImportError or ImportWarning:  # Python build-in map (serial)
            invalid_pops = list(toolbox.map(toolbox.evaluate, [cali_obj] * popnum, invalid_pops))
        for tmpind in invalid_pops:
            if step == 'Q':  # Step 1 Calibrating discharge
                tmpind.fitness.values, labels = tmpind.cali.efficiency_values('Q', object_names)
            elif step == 'SED':  # Step 2 Calibrating sediment
                sedobjvs, labels = tmpind.cali.efficiency_values('SED', object_names)
                qobjvs, qobjlabels = ind.cali.efficiency_values('Q', object_names)
                labels += [qobjlabels[0]]
                sedobjvs += [qobjvs[0]]
                tmpind.fitness.values = sedobjvs[:]
            elif step == 'NUTRIENT':  # Step 3 Calibrating NUTRIENT,TN,TP
                tnobjvs, tnobjlabels = tmpind.cali.efficiency_values('CH_TN', object_names)
                tpobjvs, tpobjlabels = tmpind.cali.efficiency_values('CH_TP', object_names)
                qobjvs, qobjlabels = ind.cali.efficiency_values('Q', object_names)
                sedobjvs, sedobjlabels = tmpind.cali.efficiency_values('SED', object_names)
                objvs = [tnobjvs[0], tpobjvs[0], qobjvs[0], sedobjvs[0]]
                labels = [tnobjlabels[0], tpobjlabels[0], qobjlabels[0], sedobjlabels[0]]
                tmpind.fitness.values = objvs[:]
        # NSE > 0 is the preliminary condition to be a valid solution!
        if filter_NSE:
            invalid_pops = [tmpind for tmpind in invalid_pops if tmpind.fitness.values[0] > 0]
            if len(invalid_pops) < 2:
                print('The initial population should be greater or equal than 2. '
                      'Please check the parameters ranges or change the sampling strategy!')
                exit(0)
        return invalid_pops, labels  # Currently, `invalid_pops` contains evaluated individuals

    # Record the count and execute timespan of model runs during the optimization
    modelruns_count = {0: len(pop)}
    modelruns_time = {0: 0.}  # Total time counted according to evaluate_parallel()
    modelruns_time_sum = {0: 0.}  # Summarize time of every model runs according to pop

    # Generation 0 before optimization
    stime = time.time()
    pop, plotlables = evaluate_parallel(pop)
    modelruns_time[0] = time.time() - stime
    for ind in pop:
        allmodels_exect.append([ind.io_time, ind.comp_time, ind.simu_time, ind.runtime])
        modelruns_time_sum[0] += ind.runtime

    # currently, len(pop) may less than pop_select_num
    pop = toolbox.select(pop, pop_select_num)
    # Output simulated data to json or pickle files for future use.
    output_population_details(pop, cfg.opt.simdata_dir, 0)

    record = stats.compile(pop)
    logbook.record(gen=0, evals=len(pop), **record)
    print_message(logbook.stream)

    # Begin the generational process
    output_str = '### Generation number: %d, Population size: %d ###\n' % (cfg.opt.ngens,
                                                                           cfg.opt.npop)
    print_message(output_str)
    UtilClass.writelog(cfg.opt.logfile, output_str, mode='replace')

    for gen in range(1, cfg.opt.ngens + 1):
        output_str = '###### Generation: %d ######\n' % gen
        print_message(output_str)

        offspring = [toolbox.clone(ind) for ind in pop]
        # method1: use crowding distance (normalized as 0~1) as eta
        # tools.emo.assignCrowdingDist(offspring)
        # method2: use the index of individual at the sorted offspring list as eta
        if len(offspring) >= 2:  # when offspring size greater than 2, mate can be done
            for i, ind1, ind2 in zip(range(len(offspring) // 2), offspring[::2], offspring[1::2]):
                if random.random() > cfg.opt.rcross:
                    continue
                eta = i
                toolbox.mate(ind1, ind2, eta, low, up)
                toolbox.mutate(ind1, eta, low, up, cfg.opt.rmut)
                toolbox.mutate(ind2, eta, low, up, cfg.opt.rmut)
                del ind1.fitness.values, ind2.fitness.values
        else:
            toolbox.mutate(offspring[0], 1., low, up, cfg.opt.rmut)
            del offspring[0].fitness.values

        # Evaluate the individuals with an invalid fitness
        invalid_ind = [ind for ind in offspring if not ind.fitness.valid]
        valid_ind = [ind for ind in offspring if ind.fitness.valid]
        if len(invalid_ind) == 0:  # No need to continue
            print_message('Note: No invalid individuals available, the NSGA2 will be terminated!')
            break

        # Write new calibrated parameters to MongoDB
        param_values = list()
        for idx, ind in enumerate(invalid_ind):
            ind.gen = gen
            ind.id = idx
            param_values.append(ind[:])
        param_values = numpy.array(param_values)
        write_param_values_to_mongodb(cfg.model.host, cfg.model.port, cfg.model.db_name,
                                      cali_obj.ParamDefs, param_values)
        # Count the model runs, and execute models
        invalid_ind_size = len(invalid_ind)
        modelruns_count.setdefault(gen, invalid_ind_size)
        stime = time.time()
        invalid_ind, plotlables = evaluate_parallel(invalid_ind)
        curtimespan = time.time() - stime
        modelruns_time.setdefault(gen, curtimespan)
        modelruns_time_sum.setdefault(gen, 0.)
        for ind in invalid_ind:
            allmodels_exect.append([ind.io_time, ind.comp_time, ind.simu_time, ind.runtime])
            modelruns_time_sum[gen] += ind.runtime

        # Select the next generation population
        tmp_pop = list()
        gen_idx = list()
        for ind in pop + valid_ind + invalid_ind:  # these individuals are all evaluated!
            # remove individuals that has a NSE < 0
            if [ind.gen, ind.id] not in gen_idx:
                if filter_NSE and ind.fitness.values[0] < 0:
                    continue
                tmp_pop.append(ind)
                gen_idx.append([ind.gen, ind.id])
        pop = toolbox.select(tmp_pop, pop_select_num)
        output_population_details(pop, cfg.opt.simdata_dir, gen)
        hyper_str = 'Gen: %d, New model runs: %d, ' \
                    'Execute timespan: %.4f, Sum of model run timespan: %.4f, ' \
                    'Hypervolume: %.4f\n' % (gen, invalid_ind_size,
                                             curtimespan, modelruns_time_sum[gen],
                                             hypervolume(pop, ref_pt))
        print_message(hyper_str)
        UtilClass.writelog(cfg.opt.hypervlog, hyper_str, mode='append')

        record = stats.compile(pop)
        logbook.record(gen=gen, evals=len(invalid_ind), **record)
        print_message(logbook.stream)

        # Plot 2D near optimal pareto front graphs,
        #   i.e., (NSE, RSR), (NSE, PBIAS), and (RSR,PBIAS)
        # And 3D near optimal pareto front graphs, i.e., (NSE, RSR, PBIAS)
        stime = time.time()
        front = numpy.array([ind.fitness.values for ind in pop])
        plot_pareto_front(front, plotlables, cfg.opt.out_dir,
                          gen, 'Near Pareto optimal solutions')
        plot_time += time.time() - stime

        # save in file
        if step == 'Q':  # Step 1 Calibrate discharge
            output_str += 'generation-calibrationID\t%s' % pop[0].cali.output_header('Q',
                                                                                     object_names,
                                                                                     'Cali')
            if cali_obj.cfg.calc_validation:
                output_str += pop[0].vali.output_header('Q', object_names, 'Vali')
        elif step == 'SED':  # Step 2 Calibrate sediment
            output_str += 'generation-calibrationID\t%s%s' % \
                          (pop[0].cali.output_header('SED', object_names, 'Cali'),
                           pop[0].cali.output_header('Q', object_names, 'Cali'))
            if cali_obj.cfg.calc_validation:
                output_str += '%s%s' % (pop[0].vali.output_header('SED', object_names, 'Vali'),
                                        pop[0].vali.output_header('Q', object_names, 'Vali'))
        elif step == 'NUTRIENT':  # Step 3 Calibrate NUTRIENT,TN,TP
            output_str += 'generation-calibrationID\t%s%s%s%s' % \
                          (pop[0].cali.output_header('CH_TN', object_names, 'Cali'),
                           pop[0].cali.output_header('CH_TP', object_names, 'Cali'),
                           pop[0].cali.output_header('Q', object_names, 'Cali'),
                           pop[0].cali.output_header('SED', object_names, 'Cali'))
            if cali_obj.cfg.calc_validation:
                output_str += '%s%s%s%s' % (
                    pop[0].vali.output_header('CH_TN', object_names, 'Vali'),
                    pop[0].vali.output_header('CH_TP', object_names, 'Vali'),
                    pop[0].vali.output_header('Q', object_names, 'Vali'),
                    pop[0].vali.output_header('SED', object_names, 'Vali'))
        output_str += 'gene_values\n'
        for ind in pop:
            if step == 'Q':  # Step 1 Calibrate discharge
                output_str += '%d-%d\t%s' % (ind.gen, ind.id,
                                             ind.cali.output_efficiency('Q', object_names))
                if cali_obj.cfg.calc_validation:
                    output_str += ind.vali.output_efficiency('Q', object_names)
            elif step == 'SED':  # Step 2 Calibrate sediment
                output_str += '%d-%d\t%s%s' % (ind.gen, ind.id,
                                               ind.cali.output_efficiency('SED', object_names),
                                               ind.cali.output_efficiency('Q', object_names))
                if cali_obj.cfg.calc_validation:
                    output_str += '%s%s' % (ind.vali.output_efficiency('SED', object_names),
                                            ind.vali.output_efficiency('Q', object_names))
            elif step == 'NUTRIENT':  # Step 3 Calibrate NUTRIENT, i.e., TN and TP
                output_str += '%d-%d\t%s%s%s%s' % (ind.gen, ind.id,
                                                   ind.cali.output_efficiency('CH_TN',
                                                                              object_names),
                                                   ind.cali.output_efficiency('CH_TP',
                                                                              object_names),
                                                   ind.cali.output_efficiency('Q', object_names),
                                                   ind.cali.output_efficiency('SED', object_names))
                if cali_obj.cfg.calc_validation:
                    output_str += '%s%s%s%s' % (ind.vali.output_efficiency('CH_TN', object_names),
                                                ind.vali.output_efficiency('CH_TP', object_names),
                                                ind.vali.output_efficiency('Q', object_names),
                                                ind.vali.output_efficiency('SED', object_names))
            output_str += str(ind)
            output_str += '\n'
        UtilClass.writelog(cfg.opt.logfile, output_str, mode='append')

        # TODO: Figure out if we should terminate the evolution

    # Plot hypervolume and newly executed model count
    plot_hypervolume_single(cfg.opt.hypervlog, cfg.opt.out_dir)

    # Save and print timespan information
    allmodels_exect = numpy.array(allmodels_exect)
    numpy.savetxt('%s/exec_time_allmodelruns.txt' % cfg.opt.out_dir,
                  allmodels_exect, delimiter=' ', fmt='%.4f')
    print_message('Running time of all SEIMS models:\n'
                  '\tIO\tCOMP\tSIMU\tRUNTIME\n'
                  'MAX\t%s\n'
                  'MIN\t%s\n'
                  'AVG\t%s\n'
                  'SUM\t%s\n' % ('\t'.join('%.3f' % v for v in allmodels_exect.max(0)),
                                 '\t'.join('%.3f' % v for v in allmodels_exect.min(0)),
                                 '\t'.join('%.3f' % v for v in allmodels_exect.mean(0)),
                                 '\t'.join('%.3f' % v for v in allmodels_exect.sum(0))))

    exec_time = 0.
    for genid, tmptime in list(modelruns_time.items()):
        exec_time += tmptime
    exec_time_sum = 0.
    for genid, tmptime in list(modelruns_time_sum.items()):
        exec_time_sum += tmptime
    allcount = 0
    for genid, tmpcount in list(modelruns_count.items()):
        allcount += tmpcount

    print_message('Initialization timespan: %.4f\n'
                  'Model execution timespan: %.4f\n'
                  'Sum of model runs timespan: %.4f\n'
                  'Plot Pareto graphs timespan: %.4f' % (init_time, exec_time,
                                                         exec_time_sum, plot_time))

    return pop, logbook


if __name__ == "__main__":
    cf, method = get_cali_config()
    cali_cfg = CaliConfig(cf, method=method)

    print_message('### START TO CALIBRATION OPTIMIZING ###')
    startT = time.time()

    fpop, fstats = main(cali_cfg)

    fpop.sort(key=lambda x: x.fitness.values)
    print_message(fstats)
    with open(cali_cfg.opt.logbookfile, 'w') as f:
        f.write(fstats.__str__())
    endT = time.time()
    print_message('### END OF CALIBRATION OPTIMIZING ###')
    print_message('Running time: %.2fs' % (endT - startT))
