#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""Plot time-series variables.
    @author   : Liangjun Zhu
    @changelog: 17-08-17  lj - redesign and rewrite the plotting program.\n
                18-01-04  lj - separate load data from MongoDB operations.\n
                18-02-01  lj - add plot of validation period.\n
                18-02-09  lj - compatible with Python3.\n
"""
from __future__ import absolute_import


import os
import sys
import datetime

if os.path.abspath(os.path.join(sys.path[0], '..')) not in sys.path:
    sys.path.insert(0, os.path.abspath(os.path.join(sys.path[0], '..')))

import matplotlib as mpl

if os.name != 'nt':  # Force matplotlib to not use any Xwindows backend.
    mpl.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from preprocess.text import DataValueFields
from postprocess.load_mongodb import ReadModelData
from postprocess.utility import read_simulation_from_txt, match_simulation_observation,\
    calculate_statistics, save_png_eps


def divtd(td1, td2):
    us1 = td1.microseconds + 1000000 * (td1.seconds + 86400 * td1.days)
    us2 = td2.microseconds + 1000000 * (td2.seconds + 86400 * td2.days)
    return float(us1) / float(us2)


class TimeSeriesPlots(object):
    """Plot time series data, e.g., flow charge, sediment charge, etc.
    """

    def __init__(self, cfg):
        """EMPTY"""
        self.ws = cfg.model_dir
        self.plot_vars = cfg.plt_vars
        self.lang_cn = cfg.lang_cn
        # UTCTIME, simulation period
        self.stime = cfg.time_start
        self.etime = cfg.time_end
        self.subbsnID = cfg.plt_subbsnid
        # add validation period, lj - 2018-2-1
        self.vali_stime = cfg.vali_stime
        self.vali_etime = cfg.vali_etime

        # Read model data from MongoDB, the time period of simulation is read from FILE_IN.
        self.readData = ReadModelData(cfg.hostname, cfg.port, cfg.spatial_db)
        self.mode = self.readData.Mode
        self.interval = self.readData.Interval
        # check start and end time of calibration
        st, et = self.readData.SimulationPeriod
        self.plot_validation = True
        if st > self.stime:
            self.stime = st
        if et < self.etime:
            self.etime = et
        if st > self.etime > self.stime:
            self.stime = st
            self.etime = et
            # in this circumstance, no validation should be calculated.
            self.vali_stime = None
            self.vali_etime = None
            self.plot_validation = False
        # check validation time period
        if self.vali_stime is not None and self.vali_etime is not None:
            if self.vali_stime >= self.vali_etime or st > self.vali_etime > self.vali_stime:
                self.vali_stime = None
                self.vali_etime = None
                self.plot_validation = False
            elif st > self.vali_stime:
                self.vali_stime = st
            elif et < self.vali_etime:
                self.vali_etime = et
        else:
            self.plot_validation = False
        # Set start time and end time of both calibration and validation periods
        start = self.stime
        end = self.etime
        if self.plot_validation:
            start = self.stime if self.stime < self.vali_stime else self.vali_stime
            end = self.etime if self.etime > self.vali_etime else self.vali_etime
        self.outletid = self.readData.OutletID
        # read precipitation
        self.pcp_date_value = self.readData.Precipitation(self.subbsnID, start, end)
        # read simulated data and update the available variables
        self.plot_vars, self.sim_data_dict = read_simulation_from_txt(self.ws, self.plot_vars,
                                                                      self.outletid,
                                                                      start, end)
        self.sim_data_value = list()
        for d, vs in self.sim_data_dict.items():
            self.sim_data_value.append([d] + vs[:])
        # reset start time and end time
        if len(self.sim_data_value) == 0:
            raise RuntimeError('No available simulate data, please check the start and end time!')
        # read observation data from MongoDB
        self.obs_vars, self.obs_data_dict = self.readData.Observation(self.subbsnID, self.plot_vars,
                                                                      start, end)

        # Calibration period
        self.sim_obs_dict = match_simulation_observation(self.plot_vars, self.sim_data_dict,
                                                         self.obs_vars, self.obs_data_dict,
                                                         start_time=self.stime, end_time=self.etime)
        calculate_statistics(self.sim_obs_dict)
        # Validation period if existed
        self.vali_sim_obs_dict = dict()
        if self.plot_validation:
            self.vali_sim_obs_dict = match_simulation_observation(self.plot_vars,
                                                                  self.sim_data_dict,
                                                                  self.obs_vars,
                                                                  self.obs_data_dict,
                                                                  start_time=self.vali_stime,
                                                                  end_time=self.vali_etime)
            calculate_statistics(self.vali_sim_obs_dict)

    def generate_plots(self):
        """Generate hydrographs of discharge, sediment, nutrient (amount or concentrate), etc."""
        # set ticks direction, in or out
        plt.rcParams['xtick.direction'] = 'out'
        plt.rcParams['ytick.direction'] = 'out'
        if self.lang_cn:
            plt.rcParams['font.family'] = 'SimSun'  # 宋体
            obs_str = u'观测值'
            sim_str = u'模拟值'
            cali_str = u'率定期'
            vali_str = u'验证期'
            pcp_str = u'降水'
            pcpaxis_str = u'降水 (mm)'
            xaxis_str = u'日期'
        else:
            plt.rcParams['font.family'] = 'Times New Roman'
            obs_str = 'Observation'
            sim_str = 'Simulation'
            cali_str = 'Calibration'
            vali_str = 'Validation'
            pcp_str = 'Precipitation'
            pcpaxis_str = 'Precipitation (mm)'
            xaxis_str = 'Date'
        plt.rcParams['mathtext.fontset'] = 'custom'
        plt.rcParams['mathtext.it'] = 'STIXGeneral:italic'
        plt.rcParams['mathtext.bf'] = 'STIXGeneral:italic:bold'
        sim_date = list(self.sim_data_dict.keys())
        for i, param in enumerate(self.plot_vars):
            # plt.figure(i)
            fig, ax = plt.subplots(figsize=(12, 4))
            ylabel_str = param
            if param in ['Q', 'QI', 'QG', 'QS']:
                ylabel_str += ' (m$^3$/s)'
            elif 'CONC' in param.upper():  # Concentrate
                if 'SED' in param.upper():
                    ylabel_str += ' (g/L)'
                else:
                    ylabel_str += ' (mg/L)'
            else:  # amount
                ylabel_str += ' (kg)'

            obs_dates = None
            obs_values = None
            if self.sim_obs_dict and param in self.sim_obs_dict:
                obs_dates = self.sim_obs_dict[param][DataValueFields.utc]
                obs_values = self.sim_obs_dict[param]['Obs']
            # append validation data
            if self.vali_sim_obs_dict and param in self.vali_sim_obs_dict:
                obs_dates += self.vali_sim_obs_dict[param][DataValueFields.utc]
                obs_values += self.vali_sim_obs_dict[param]['Obs']
            if obs_values is not None:
                # bar graph
                p1 = ax.bar(obs_dates, obs_values, label=obs_str, color='none',
                            edgecolor='black',
                            linewidth=0.5, align='center', hatch='//')
                # # line graph
                # p1, = ax.plot(obs_dates, obs_values, label=obs_str, color='black', marker='+',
                #              markersize=2, linewidth=1)
            sim_list = [v[i + 1] for v in self.sim_data_value]
            p2, = ax.plot(sim_date, sim_list, label=sim_str, color='red',
                          marker='+', markersize=2, linewidth=0.8)
            plt.xlabel(xaxis_str)
            # format the ticks date axis
            date_fmt = mdates.DateFormatter('%m-%d-%y')
            # autodates = mdates.AutoDateLocator()
            # days = mdates.DayLocator(bymonthday=range(1, 32), interval=4)
            # months = mdates.MonthLocator()
            # ax.xaxis.set_major_locator(months)
            ax.xaxis.set_major_formatter(date_fmt)
            # ax.xaxis.set_minor_locator(days)
            ax.tick_params('both', length=5, width=2, which='major')
            ax.tick_params('both', length=3, width=1, which='minor')
            ax.set_xlim(left=self.sim_data_value[0][0], right=self.sim_data_value[-1][0])
            fig.autofmt_xdate(rotation=0, ha='center')

            plt.ylabel(ylabel_str)
            # plt.legend(bbox_to_anchor = (0.03, 0.85), loc = 2, shadow = True)
            if obs_values is not None:
                ymax = max(max(sim_list), max(obs_values)) * 1.8
                ymin = min(min(sim_list), min(obs_values)) * 0.8
            else:
                ymax = max(sim_list) * 1.8
                ymin = min(sim_list) * 0.8
            ax.set_ylim(float(ymin), float(ymax))
            ax2 = ax.twinx()
            ax.tick_params(axis='x', which='both', bottom='on', top='off')
            ax2.tick_params(axis='y', length=5, width=2, which='major')
            ax2.set_ylabel(pcpaxis_str)

            pcp_date = [v[0] for v in self.pcp_date_value]
            preci = [v[1] for v in self.pcp_date_value]
            p3 = ax2.bar(pcp_date, preci, label=pcp_str, color='blue', linewidth=0,
                         align='center')
            ax2.set_ylim(float(max(preci)) * 1.8, float(min(preci)) * 0.8)
            # draw a dash line to separate calibration and validation period
            delta_dt = (self.sim_data_value[-1][0] - self.sim_data_value[0][0]) // 9
            delta_dt2 = (self.sim_data_value[-1][0] - self.sim_data_value[0][0]) // 35
            # by default, separate time line is the end of calibration period
            sep_time = self.etime
            time_pos = [sep_time - delta_dt]
            ymax, ymin = ax2.get_ylim()
            yc = abs(ymax - ymin) / 4.
            if self.plot_validation:
                sep_time = self.vali_stime  # by default, validation period after calibration
                cali_vali_labels = [cali_str, vali_str]
                if self.vali_stime < self.stime:
                    sep_time = self.stime
                    cali_vali_labels = [vali_str, cali_str]
                    # time_pos = [sep_time + delta_dt2, sep_time - delta_dt]
                time_pos = [sep_time - delta_dt, sep_time + delta_dt2]
                sep_time = self.vali_stime if self.vali_stime > self.stime else self.stime
                ax.axvline(sep_time, color='black', linestyle='dashed', linewidth=2)
                plt.text(time_pos[0], yc, cali_vali_labels[0],
                         fontdict={'style': 'italic', 'weight': 'bold'}, color='black')
                plt.text(time_pos[1], yc, cali_vali_labels[1],
                         fontdict={'style': 'italic', 'weight': 'bold'}, color='black')
            # set legend and labels
            if obs_values is None or len(obs_values) < 2:
                leg = ax.legend([p3, p2], [pcp_str, sim_str], ncol=2,
                                bbox_to_anchor=(0., 1.02, 1., 0.102),
                                borderaxespad=0.2,
                                loc='lower left', fancybox=True)
                # plt.tight_layout(rect=(0, 0, 1, 0.93))
            else:
                leg = ax.legend([p3, p1, p2], [pcp_str, obs_str, sim_str],
                                bbox_to_anchor=(0., 1.02, 1., 0.102),
                                borderaxespad=0.,
                                ncol=3, loc='lower left', fancybox=True)
                try:
                    nse = self.sim_obs_dict[param]['NSE']
                    r2 = self.sim_obs_dict[param]['R-square']
                    pbias = self.sim_obs_dict[param]['PBIAS']
                    rsr = self.sim_obs_dict[param]['RSR']
                    cali_txt = '$\mathit{NSE}$: %.2f\n$\mathit{RSR}$: %.2f\n' \
                               '$\mathit{PBIAS}$: %.2f%%\n$\mathit{R^2}$: %.2f' % \
                               (nse, rsr, pbias, r2)
                    plt.text(time_pos[0], yc * 2.5, cali_txt, color='red')
                    if self.plot_validation and self.vali_sim_obs_dict:
                        nse = self.vali_sim_obs_dict[param]['NSE']
                        r2 = self.vali_sim_obs_dict[param]['R-square']
                        pbias = self.vali_sim_obs_dict[param]['PBIAS']
                        rsr = self.vali_sim_obs_dict[param]['RSR']
                        vali_txt = '$\mathit{NSE}$: %.2f\n$\mathit{RSR}$: %.2f\n' \
                                   '$\mathit{PBIAS}$: %.2f%%\n$\mathit{R^2}$: %.2f' % \
                                   (nse, rsr, pbias, r2)
                        plt.text(time_pos[1], yc * 2.5, vali_txt, color='red')

                except ValueError or Exception:
                    pass
            plt.tight_layout(rect=(0, 0, 1, 0.93))
            leg.get_frame().set_alpha(0.5)
            # plt.title(param, color='#aa0903')
            timerange = '%s-%s' % (self.sim_data_value[0][0].strftime('%Y-%m-%d'),
                                   self.sim_data_value[-1][0].strftime('%Y-%m-%d'))
            save_png_eps(plt, self.ws, param + '-' + timerange)
