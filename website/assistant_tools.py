"""Small, validated lookup tools. The model cannot submit SQL or choose file paths."""
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PKT = ZoneInfo('Asia/Karachi')
METRICS = {
    'temperature_c':('temperature_c','weather_dq_status','degrees Celsius'),
    'humidity_pct':('humidity_pct','weather_dq_status','percent'),
    'wind_speed_ms':('wind_speed_ms','weather_dq_status','metres per second'),
    'heat_index_c':(None,'weather_dq_status','degrees Celsius'),
    'dew_point_c':('dew_point_c','weather_dq_status','degrees Celsius'),
    'weather_code':(None,None,'WMO weather code'),
    'precipitation_probability':(None,None,'percent chance of precipitation'),
    'us_aqi':(None,None,'US AQI index'),
    'pm2_5':('pm25_ug_m3','cams_dq_status','micrograms per cubic metre'),
    'pm10':('pm10_ug_m3','cams_dq_status','micrograms per cubic metre'),
    'nitrogen_dioxide':('no2_ug_m3','cams_dq_status','micrograms per cubic metre'),
    'ozone':('ozone_ug_m3','cams_dq_status','micrograms per cubic metre'),
}

DECLARATIONS = [
    {'name':'lookup_environment','description':'Fetch only requested weather/air metrics. Current values, compact future forecast, or read-only database history for explicit Pakistan dates. No archived AQI scores. Missing dates return coverage.',
     'parameters':{'type':'OBJECT','properties':{
         'city':{'type':'STRING','description':'Supported city; default selected city.'},
         'period':{'type':'STRING','enum':['current','forecast','history']},
         'metrics':{'type':'ARRAY','items':{'type':'STRING','enum':list(METRICS)},'description':'One to four metrics only.'},
         'start_date':{'type':'STRING','description':'History start date YYYY-MM-DD, explicit year.'},
         'end_date':{'type':'STRING','description':'Inclusive history end; default start. Maximum 366 days.'},
         'hour':{'type':'INTEGER','description':'Optional single-day hour 0–23 Pakistan time.'},
         'hours':{'type':'INTEGER','description':'Forecast horizon 1–48; default 24.'}},'required':['period','metrics']}},
    {'name':'get_model_insights','description':'Fetch the saved city ML prediction, target, freshness and small historical evaluation. This does not run a model.',
     'parameters':{'type':'OBJECT','properties':{'city':{'type':'STRING'}}}},
    {'name':'search_safety_guidance','description':'Retrieve two short relevant official passages for heat safety, humidity, hydration, rest or particle explanations.',
     'parameters':{'type':'OBJECT','properties':{'query':{'type':'STRING'}},'required':['query']}},
    {'name':'get_project_info','description':'Explain data sources, available cities, refreshing, storage and site navigation.',
     'parameters':{'type':'OBJECT','properties':{}}},
]


def clean(value):
    if isinstance(value,datetime):return value.astimezone(PKT).isoformat()
    if isinstance(value,float):return round(value,3) if math.isfinite(value) else None
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    return value


class Toolbox:
    def __init__(self, city, read_dashboard, get_agent, cities):
        self.city=city
        self.read_dashboard=read_dashboard
        self.get_agent=get_agent
        self.cities={c['id']:c for c in cities()}
        self.sources={}
        self.evidence={}
        self.dashboards={}

    def dashboard(self,city):
        if city not in self.dashboards:
            self.dashboards[city]=self.read_dashboard(live=True,city=city)
        return self.dashboards[city]

    def source(self,id,title,data,url=None):
        self.sources[id]={'id':id,'title':title,'url':url}
        self.evidence[id]=data
        return {'source_id':id,'data':clean(data)}

    def execute(self,name,args):
        allowed={d['name']:set(d['parameters']['properties']) for d in DECLARATIONS}
        if name not in allowed or not isinstance(args,dict) or set(args)-allowed[name]:
            return {'error':'Unknown tool or unsupported parameters.'}
        try:
            city=args.get('city',self.city)
            if not isinstance(city,str) or city.casefold() not in self.cities:
                return {'error':'Choose one of the supported cities: '+', '.join(self.cities)}
            args={**args}
            if 'city' in args:args['city']=city.casefold()
            result=getattr(self,name)(**args)
            result=clean(result)
            # Tool replies are deliberately small; never send full arrays or warehouses.
            if len(json.dumps(result,ensure_ascii=False))>3000:
                data=result.get('data',{})
                data.pop('series',None)
                data['detail_omitted']='Series omitted to keep the answer context small.'
                if len(json.dumps(result,ensure_ascii=False))>3000:
                    return {'error':'Please request fewer metrics or a shorter period.'}
            return result
        except (ValueError,TypeError,KeyError):
            return {'error':'Invalid lookup parameters. Use valid dates, supported metrics and a short date range.'}
        except Exception:
            return {'error':'The requested source could not be read. Please try another date or retry shortly.'}

    def lookup_environment(self,period,metrics,city=None,start_date='',end_date='',hour=None,hours=24):
        city=city or self.city
        if (not isinstance(metrics,list) or not 1<=len(metrics)<=4
                or any(not isinstance(m,str) or m not in METRICS for m in metrics)):
            raise ValueError('Unsupported metrics')
        metrics=list(dict.fromkeys(metrics))
        if period=='history':return self.history(city,metrics,start_date,end_date,hour)
        if period not in ('current','forecast') or type(hours) is not int or not 1<=hours<=48:
            raise ValueError('Invalid period')
        dashboard=self.dashboard(city)
        result={'city':self.cities[city]['name'],'period':period,'model_estimates_not_sensors':True,'metrics':{}}
        feed_types=set()
        for metric in metrics:
            feed=dashboard['air_quality' if metric in ('us_aqi','pm2_5','pm10','nitrogen_dioxide','ozone') else 'weather']
            feed_types.add('air' if metric in ('us_aqi','pm2_5','pm10','nitrogen_dioxide','ozone') else 'weather')
            info={'unit':METRICS[metric][2],'source':feed['source'],'status':feed['status'],'valid_at':feed['valid_at_utc'],'pulled_at':feed['fetched_at_utc']}
            if period=='current':info['value']=feed['current'].get(metric)
            else:
                rows=feed.get('hourly',[])
                if rows:
                    end=rows[0]['time_utc']+timedelta(hours=hours)
                    rows=[r for r in rows if r['time_utc']<=end and isinstance(r.get(metric),(float,int))]
                if rows:
                    low=min(rows,key=lambda r:r[metric]);high=max(rows,key=lambda r:r[metric])
                    info.update(minimum=low[metric],minimum_at=low['time_utc'],maximum=high[metric],maximum_at=high['time_utc'],
                                mean=sum(r[metric] for r in rows)/len(rows),points=len(rows))
                else:info['value']=None
            if metric=='us_aqi':info['latest_category']=feed['current'].get('aqi_category')
            result['metrics'][metric]=info
        id=f'{city}:{period}:'+','.join(metrics)
        source_name='Open-Meteo weather + CAMS air quality' if len(feed_types)>1 else 'CAMS air quality' if 'air' in feed_types else 'Open-Meteo weather'
        url=None if len(feed_types)>1 else 'https://open-meteo.com/en/docs/air-quality-api' if 'air' in feed_types else 'https://open-meteo.com/en/docs'
        return self.source(id,f"{self.cities[city]['name']} · {period} · {source_name}",result,url)

    def history(self,city,metrics,start_date,end_date='',hour=None):
        start=datetime.strptime(start_date,'%Y-%m-%d').replace(tzinfo=PKT)
        end=datetime.strptime(end_date or start_date,'%Y-%m-%d').replace(tzinfo=PKT)+timedelta(days=1)
        if not 0<(end-start).days<=366:raise ValueError('Invalid range')
        if hour is not None:
            if type(hour) is not int or not 0<=hour<=23 or (end-start).days!=1:raise ValueError('Invalid hour')
            start+=timedelta(hours=hour);end=start+timedelta(hours=1)
        result={'city':self.cities[city]['name'],'start':start,'end_exclusive':end,'source':'Bundled DuckDB gold.hourly_evidence',
                'kind':'historical reanalysis / CAMS concentrations, not a live forecast','metrics':{}}
        if city!='karachi':
            result.update(available=False,note='No city-specific historical hourly records are bundled for this city. Karachi records cannot be used for it.')
        else:
            import duckdb
            with duckdb.connect(str(ROOT/'warehouse/heatsafe_live.duckdb'),read_only=True) as db:
                for metric in metrics:
                    column,quality,unit=METRICS[metric]
                    info={'unit':unit}
                    if metric in ('us_aqi','weather_code','precipitation_probability'):
                        info.update(records=0,note='This field is not stored in the historical archive. Pollutant concentrations are not AQI scores.')
                        result['metrics'][metric]=info;continue
                    if metric=='heat_index_c':
                        # Derived using the same project formula, never substituted for saved ML inference.
                        if __package__:
                            from .server import heat_index
                        else:
                            from server import heat_index
                        raw=db.execute("SELECT time_utc, temperature_c, humidity_pct FROM gold.hourly_evidence WHERE weather_dq_status='PASS' AND time_utc>=? AND time_utc<? ORDER BY time_utc",[start,end]).fetchall()
                        rows=[(t,heat_index(temp,rh)) for t,temp,rh in raw if temp is not None and rh is not None]
                        column='temperature_c'
                    else:
                        rows=db.execute(f"SELECT time_utc, {column} FROM gold.hourly_evidence WHERE {quality}='PASS' AND {column} IS NOT NULL AND time_utc>=? AND time_utc<? ORDER BY time_utc",[start,end]).fetchall()
                    span=db.execute(f"SELECT min(time_utc),max(time_utc) FROM gold.hourly_evidence WHERE {quality}='PASS' AND {column} IS NOT NULL").fetchone()
                    info.update(records=len(rows),available_from=span[0],available_until=span[1])
                    if rows:
                        low=min(rows,key=lambda r:r[1]);high=max(rows,key=lambda r:r[1])
                        info.update(mean=sum(r[1] for r in rows)/len(rows),minimum=low[1],minimum_at=low[0],maximum=high[1],maximum_at=high[0])
                        if hour is not None:info['value']=rows[0][1]
                    result['metrics'][metric]=info
                result['available']=any(v.get('records',0) for v in result['metrics'].values())
        id=f'{city}:history:{start_date}:{end_date or start_date}'+(':'+str(hour) if hour is not None else '')
        return self.source(id,f"{self.cities[city]['name']} · database lookup · {start_date}"+(f' to {end_date}' if end_date and end_date!=start_date else ''),result)

    def get_model_insights(self,city=None):
        city=city or self.city
        data=self.dashboard(city)
        f=data['forecast'];m=data['metrics']
        result={'city':self.cities[city]['name'],'predicted_quantity':'heat index, not air temperature',
                'available':f.get('available',True),'value_c':f.get('model_prediction_c'),'target_time':f.get('target_time_utc'),
                'risk_category':f.get('model_risk_category'),'interval_c':[f.get('interval_lower_c'),f.get('interval_upper_c')],
                'fresh':data['fresh'],'collected_at':data['freshness']['collected_at_utc'],
                'evaluation':{k:m.get(k) for k in ('test_model_mae_c','test_model_r2','test_rows','test_baseline_mae_c')},
                'risk_classification_accuracy':data['risk_metrics'].get('accuracy'),
                'note':'One saved result. If fresh=false it is out of date, not a newly generated prediction.'}
        return self.source(f'{city}:forecast',f"{self.cities[city]['name']} · saved ML result and evaluation",result)

    def search_safety_guidance(self,query):
        if not isinstance(query,str) or not 1<=len(query)<=500:raise ValueError('Invalid query')
        from sklearn.metrics.pairwise import cosine_similarity
        base=self.get_agent();scores=cosine_similarity(base.vectorizer.transform([query]),base.matrix)[0]
        ranked=sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)
        passages=[]
        for i in ranked[:2]:
            if scores[i]<.04:continue
            chunk=base.chunks[i];id='guidance:'+chunk['chunk_id']
            self.sources[id]={'id':id,'title':chunk.get('section_title') or chunk.get('title') or chunk['chunk_id'],'url':chunk['source_url']}
            passages.append({'source_id':id,'text':chunk['text'][:700]})
        return {'passages':passages,'note':'Only these excerpts were retrieved; no matching passage means insufficient evidence.'}

    def get_project_info(self):
        return self.source('project:info','HeatSafe data and navigation',{
            'cities':[c['name'] for c in self.cities.values()], 'time_zone':'Pakistan (UTC+5)',
            'updates':'Page checks every 5 minutes; API cache 15 minutes per instance. DuckDB is read-only and updates by redeployment. No scheduled Prefect job on Vercel.',
            'tabs':'Overview: summary. Forecast: weather timeline and saved ML. Air quality: AQI, particles, archive. Network: connected evidence. Sources: provenance and evaluations.',
            'sources':'Open-Meteo modeled weather; CAMS modeled air quality; NIOSH/HSE/NOAA safety passages.',
            'limits':'No hospital records. City-specific historical records are only available where explicitly bundled.'})
