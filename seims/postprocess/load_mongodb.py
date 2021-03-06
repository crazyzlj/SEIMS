#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""Load data from MongoDB.
   Note that, the ReadModelData class is not picklable,
     since MongoClient returns thread.lock objects.
    @author   : Liangjun Zhu
    @changelog: 18-01-02  - lj - separated from plot_timeseries.\n
                18-02-09  - lj - compatible with Python3.\n
"""
from __future__ import absolute_import

import os
import sys
from collections import OrderedDict

from pygeoc.utils import StringClass, text_type

if os.path.abspath(os.path.join(sys.path[0], '..')) not in sys.path:
    sys.path.insert(0, os.path.abspath(os.path.join(sys.path[0], '..')))

from preprocess.db_mongodb import ConnectMongoDB, MongoQuery
from preprocess.text import DBTableNames, ModelCfgFields, FieldNames, SubbsnStatsName, \
    DataValueFields, DataType, StationFields


class ReadModelData(object):
    def __init__(self, host, port, dbname):
        """Initialization."""
        client = ConnectMongoDB(host, port)
        conn = client.get_conn()
        self.maindb = conn[dbname]
        self.filein_tab = self.maindb[DBTableNames.main_filein]
        self._climdb_name = self.HydroClimateDBName
        self.climatedb = conn[self._climdb_name]
        self._mode = ''
        self._interval = -1
        # UTCTIME
        self._stime = None
        self._etime = None
        self._outletid = -1

    @property
    def HydroClimateDBName(self):
        climtbl = self.maindb[DBTableNames.main_sitelist]
        allitems = climtbl.find()
        if not allitems.count():
            raise RuntimeError('%s Collection is not existed or empty!' %
                               DBTableNames.main_sitelist)
        for item in allitems:
            if FieldNames.db in item:
                self._climdb_name = item.get(FieldNames.db)
                break
        return self._climdb_name

    @property
    def Mode(self):
        """Get simulation mode."""
        if self._mode != '':
            return self._mode.upper()
        mode_dict = self.filein_tab.find_one({ModelCfgFields.tag: FieldNames.mode})
        self._mode = mode_dict[ModelCfgFields.value]
        if isinstance(self._mode, text_type):
            self._mode = str(self._mode)
        return self._mode.upper()

    @property
    def Interval(self):
        if self._interval > 0:
            return self._interval
        findinterval = self.filein_tab.find_one({ModelCfgFields.tag: ModelCfgFields.interval})
        self._interval = int(findinterval[ModelCfgFields.value])
        return self._interval

    @property
    def OutletID(self):
        if self._outletid > 0:
            return self._outletid
        self._outletid = int(MongoQuery.get_init_parameter_value(self.maindb,
                                                                 SubbsnStatsName.outlet))
        return self._outletid

    @property
    def SimulationPeriod(self):
        if self._stime is not None and self._etime is not None:
            return self._stime, self._etime
        st = self.filein_tab.find_one({ModelCfgFields.tag: ModelCfgFields.stime})[
            ModelCfgFields.value]
        et = self.filein_tab.find_one({ModelCfgFields.tag: ModelCfgFields.etime})[
            ModelCfgFields.value]
        st = StringClass.get_datetime(st)
        et = StringClass.get_datetime(et)
        if self._stime is None or st > self._stime:
            self._stime = st
        if self._etime is None or et < self._etime:
            self._etime = et
        if st > self._etime > self._stime:
            self._stime = st
            self._etime = et
        return self._stime, self._etime

    def Precipitation(self, subbsn_id, start_time, end_time):
        """
        The precipitation is read according to the subbasin ID.
            Especially when plot a specific subbasin (such as ID 3).
            For the whole basin, the subbasin ID is 0.
        Returns:
            Precipitation data list with the first element as datetime.
            [[Datetime1, value1], [Datetime2, value2], ..., [Datetimen, valuen]]
        """
        pcp_date_value = list()
        sitelist_tab = self.maindb[DBTableNames.main_sitelist]
        findsites = sitelist_tab.find_one({FieldNames.subbasin_id: subbsn_id,
                                           FieldNames.mode: self.Mode})
        if findsites is not None:
            site_liststr = findsites[FieldNames.site_p]
        else:
            raise RuntimeError('Cannot find precipitation site for subbasin %d.' % subbsn_id)
        site_list = StringClass.extract_numeric_values_from_string(site_liststr)
        site_list = [int(v) for v in site_list]
        if len(site_list) == 0:
            raise RuntimeError('Cannot find precipitation site for subbasin %d.' % subbsn_id)

        pcp_dict = OrderedDict()

        for pdata in self.climatedb[DBTableNames.data_values].find(
                {DataValueFields.utc: {"$gte": start_time, '$lte': end_time},
                 DataValueFields.type: DataType.p,
                 DataValueFields.id: {"$in": site_list}}).sort([(DataValueFields.utc, 1)]):
            curt = pdata[DataValueFields.utc]
            curv = pdata[DataValueFields.value]
            if curt not in pcp_dict:
                pcp_dict[curt] = 0.
            pcp_dict[curt] += curv
        # average
        if len(site_list) > 1:
            for t in pcp_dict:
                pcp_dict[t] /= len(site_list)
        for t, v in pcp_dict.items():
            # print(str(t), v)
            pcp_date_value.append([t, v])
        print('Read precipitation from %s to %s done.' % (start_time.strftime('%c'),
                                                          end_time.strftime('%c')))
        return pcp_date_value

    def Observation(self, subbsn_id, vars, start_time, end_time):
        """Read observation data of given variables.

        Changelog:
          - 1. 2018-8-29 Use None when the observation of one variables is absent.

        Returns:
            1. Observed variable names, [var1, var2, ...]
            2. Observed data dict of selected plotted variables, with UTCDATETIME.
               {Datetime: [value_of_var1, value_of_var2, ...], ...}
        """
        vars_existed = list()
        data_dict = OrderedDict()

        coll_list = self.climatedb.collection_names()
        if DBTableNames.observes not in coll_list:
            return None, None
        isoutlet = 0
        if subbsn_id == self.OutletID:
            isoutlet = 1

        def get_observed_name(name):
            """To avoid the prefix of subbasin number."""
            if '_' in name:
                name = name.split('_')[1]
            return name

        def get_basename(name):
            """Get base variable name, e.g., SED for SED and SEDConc."""
            name = get_observed_name(name)
            if 'Conc' in name:
                name = name.split('Conc')[0]
            return name

        siteTbl = self.climatedb[DBTableNames.sites]
        obsTbl = self.climatedb[DBTableNames.observes]
        for i, param_name in enumerate(vars):
            site_items = siteTbl.find_one({StationFields.type: get_basename(param_name),
                                           StationFields.outlet: isoutlet,
                                           StationFields.subbsn: subbsn_id})

            if site_items is None:
                continue
            site_id = site_items.get(StationFields.id)
            for obs in obsTbl.find({DataValueFields.utc: {"$gte": start_time, '$lte': end_time},
                                    DataValueFields.type: get_observed_name(param_name),
                                    DataValueFields.id: site_id}).sort([(DataValueFields.utc, 1)]):

                if param_name not in vars_existed:
                    vars_existed.append(param_name)
                curt = obs[DataValueFields.utc]
                curv = obs[DataValueFields.value]
                if curt not in data_dict:
                    data_dict[curt] = [None] * len(vars)
                data_dict[curt][i] = curv
        if not vars_existed:
            return None, None
        # remove the redundant None in data_dict, in case of len(vars_existed) != len(vars)
        for i, vname in enumerate(vars):
            if vname in vars_existed:
                continue
            for dt, adata in list(data_dict.items()):
                del adata[i]

        print('Read observation data of %s from %s to %s done.' % (','.join(vars_existed),
                                                                   start_time.strftime('%c'),
                                                                   end_time.strftime('%c')))
        return vars_existed, data_dict


def main():
    """Functional tests."""
    import datetime

    host = '192.168.253.203'
    port = 27018
    dbname = 'youwuzhen10m_longterm_model'
    stime = datetime.datetime(2013, 1, 1, 0, 0)
    etime = datetime.datetime(2013, 12, 31, 0, 0)
    rd = ReadModelData(host, port, dbname)
    print(rd.HydroClimateDBName)
    print(rd.Precipitation(4, stime, etime))
    print(rd.Observation(4, ['Q'], stime, etime))


if __name__ == "__main__":
    main()
