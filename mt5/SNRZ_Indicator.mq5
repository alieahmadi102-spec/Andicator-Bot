//+------------------------------------------------------------------+
//|                                               SNRZ_Indicator.mq5 |
//|        SNRZ Strategy — "Zindan The Gold Chaser" Masterclass      |
//|                                                                  |
//|  Implements on MetaTrader 5:                                     |
//|   • Valid Support / Valid Resistance (two-movement rule)         |
//|   • 75% breakout rule (body + directional shadow only)           |
//|   • Inversion: RBS / SBR (fresh) , I.VR / I.VS (valid) zones     |
//|   • SRR / RSS (support / resistance that broke >= 2 opposites)   |
//|   • PO2 — 2nd retest of an INVERSION zone (strongest entry)      |
//|   • SNRZ engulfing / pin-bar confirmation                        |
//|   • Book timeframe table: zones + trend on the ANALYSIS TF,      |
//|     confirmation on the chart TF                                 |
//|   • One position at a time with SL / TP1 / TP2 / TP3 drawn       |
//+------------------------------------------------------------------+
#property copyright   "SNRZ (Zindan The Gold Chaser) — indicator port"
#property version     "5.20"
#property description "SNRZ zones on the higher analysis timeframe, confirmation on the chart, one trade at a time"
#property indicator_chart_window
#property indicator_buffers 4
#property indicator_plots   4

// BUY arrow
#property indicator_label1  "SNRZ Buy"
#property indicator_type1   DRAW_ARROW
#property indicator_color1  clrMediumSeaGreen
#property indicator_width1  2
// SELL arrow
#property indicator_label2  "SNRZ Sell"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrTomato
#property indicator_width2  2
// PO2 BUY arrow
#property indicator_label3  "SNRZ PO2 Buy"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrLime
#property indicator_width3  3
// PO2 SELL arrow
#property indicator_label4  "SNRZ PO2 Sell"
#property indicator_type4   DRAW_ARROW
#property indicator_color4  clrRed
#property indicator_width4  3

//--- inputs -----------------------------------------------------------------
input group "Zones (SNRZ)"
input ENUM_TIMEFRAMES InpZoneTF = PERIOD_CURRENT; // Zone timeframe (CURRENT = AUTO from book table)
input int    InpPivotLen     = 10;    // Pivot length (swing sensitivity)
input int    InpMaxZones     = 6;     // Max active zones
input double InpBigMoveATR   = 1.5;   // "Big Movement" >= ATR x
input double InpBreakoutPct  = 75.0;  // Breakout rule (%) — the 75% rule
input double InpMinZoneATR   = 0.15;  // Min zone height (ATR x)
input double InpMaxZoneATR   = 1.20;  // Max zone height (ATR x)
input int    InpZoneMaxAge   = 400;   // Zone lifetime (analysis-TF bars)
input double InpMaxZoneDistATR = 5.0; // Drop zones further than (analysis ATR x)

input group "Signals"
input bool   InpTrendFilter  = true;  // Trade only with structure trend
input bool   InpAllowCounterInv = false; // Allow counter-trend entries on inversion zones
input bool   InpNeedConfirm  = true;  // Require confirmation candle
input int    InpMaxTouches   = 3;     // Max touches per zone (3-touch rule)
input bool   InpNeedReject   = true;  // Confirmation candle must close OUTSIDE the zone
input int    InpRangeBars    = 10;    // Range lockout (analysis-TF bars since opposite BOS)
input bool   InpOneTrade     = true;  // One trade at a time (no overtrade)
input int    InpMaxTradeBars = 300;   // Close an open trade after N chart bars
input double InpMinSlATR     = 0.5;   // Minimum stop distance (analysis ATR x)
input double InpTpMaxR       = 6.0;   // Max R for TP1/TP2 (farther zone -> TP3)
input bool   InpShowPosition = true;  // Draw Entry / SL / TP1-3 of the last setup

input group "Alerts"
input bool   InpAlertPopup   = true;  // Alert window
input bool   InpAlertPush    = false; // Push notification to phone

input group "Style"
input color  InpColSup       = C'8,153,129';   // Support zone
input color  InpColRes       = C'242,54,69';   // Resistance zone
input color  InpColInv       = C'212,175,55';  // Inversion zone (Zindan gold)
input uchar  InpFillAlpha    = 40;             // (reserved)

//--- buffers ----------------------------------------------------------------
double BufBuy[], BufSell[], BufPO2Buy[], BufPO2Sell[];

//--- zone storage -----------------------------------------------------------
struct SZone
  {
   double   top;
   double   bot;
   int      role;      // 1 = Support, -1 = Resistance
   int      state;     // 0 fresh, 1 VALID, 2 inverted
   int      touches;
   int      oppBreaks; // opposite zones broken since creation (SRR/RSS)
   bool     srr;       // qualified as SRR (support) / RSS (resistance)
   bool     wasValid;
   bool     dead;      // 3-touch rule exhausted -> no more trades
   int      sigTouch;  // anti-spam latch: one signal per touch
   int      bornH;     // analysis-TF index it was born on
   datetime bornTime;  // pivot time — where the rectangle starts
   datetime activeFrom;// no touches counted before this time
   long     id;        // object id
   bool     inZonePrev;
  };
SZone  g_zones[];
long   g_zoneSeq = 0;

int    g_atrHandle    = INVALID_HANDLE;   // chart-TF ATR
int    g_atrHtfHandle = INVALID_HANDLE;   // analysis-TF ATR
ENUM_TIMEFRAMES g_atf = PERIOD_CURRENT;
string g_prefix       = "SNRZ_";

//--- structure trend (analysis timeframe) -----------------------------------
double g_lastHigh = 0, g_prevHigh = 0, g_lastLow = 0, g_prevLow = 0;
int    g_trendState = 0;                  // 1 up · -1 down · 0 undecided
int    g_lastBosUpH = -999999, g_lastBosDnH = -999999;
bool   g_bosUpPrev = false, g_bosDnPrev = false;

//--- active position --------------------------------------------------------
bool     g_posOn = false, g_posBuy = false, g_posPO2 = false, g_posSwing = false;
double   g_posEntry = 0, g_posSL = 0, g_posTP1 = 0, g_posTP2 = 0, g_posTP3 = 0;
int      g_posStat = 0;                   // 0 running · 1/2/3 TP hit · -1 stopped
int      g_posBar  = 0;
datetime g_posTime = 0;
string   g_posZone = "";

//+------------------------------------------------------------------+
//| The book marks zones only on Weekly / Daily / 4H / 1H, and        |
//| "Timeframe = Pips". So the analysis timeframe is ONE rung up that  |
//| ladder {15m, 1H, 4H, D, W}:                                       |
//|   1m/5m -> 15m · 15m/30m -> 1H · 1H -> 4H · 4H -> D · D -> W      |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES AnalysisTF()
  {
   if(InpZoneTF != PERIOD_CURRENT)
      return InpZoneTF;
   int m = PeriodSeconds(_Period) / 60;
   if(m <= 5)    return PERIOD_M15;
   if(m <= 30)   return PERIOD_H1;
   if(m <= 60)   return PERIOD_H4;
   if(m <= 240)  return PERIOD_D1;
   if(m <= 1440) return PERIOD_W1;
   return PERIOD_MN1;
  }
//+------------------------------------------------------------------+
int OnInit()
  {
   SetIndexBuffer(0, BufBuy,     INDICATOR_DATA);
   SetIndexBuffer(1, BufSell,    INDICATOR_DATA);
   SetIndexBuffer(2, BufPO2Buy,  INDICATOR_DATA);
   SetIndexBuffer(3, BufPO2Sell, INDICATOR_DATA);

   PlotIndexSetInteger(0, PLOT_ARROW, 233);  // up arrow
   PlotIndexSetInteger(1, PLOT_ARROW, 234);  // down arrow
   PlotIndexSetInteger(2, PLOT_ARROW, 225);  // thick up
   PlotIndexSetInteger(3, PLOT_ARROW, 226);  // thick down

   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(2, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(3, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   g_atf = AnalysisTF();

   g_atrHandle    = iATR(_Symbol, _Period, 14);
   g_atrHtfHandle = iATR(_Symbol, g_atf,   14);
   if(g_atrHandle == INVALID_HANDLE || g_atrHtfHandle == INVALID_HANDLE)
      return INIT_FAILED;

   IndicatorSetString(INDICATOR_SHORTNAME,
                      "SNRZ [Zindan] " + EnumToString(g_atf) + " zones");
   return INIT_SUCCEEDED;
  }
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ObjectsDeleteAll(0, g_prefix);
   if(g_atrHandle != INVALID_HANDLE)
      IndicatorRelease(g_atrHandle);
   if(g_atrHtfHandle != INVALID_HANDLE)
      IndicatorRelease(g_atrHtfHandle);
  }
//+------------------------------------------------------------------+
//| Rectangle helpers                                                 |
//+------------------------------------------------------------------+
string ZoneName(const long id)     { return g_prefix + "Z_" + (string)id; }
string ZoneLblName(const long id)  { return g_prefix + "L_" + (string)id; }

color ZoneColor(const SZone &z)
  {
   if(z.state == 2) return InpColInv;
   return z.role == 1 ? InpColSup : InpColRes;
  }

string ZoneText(const SZone &z)
  {
   string base;
   if(z.role == 1)
      base = (z.state == 2 ? (z.wasValid ? "I.VR" : "RBS") : (z.srr ? "SRR" : (z.state == 1 ? "V.S" : "S")));
   else
      base = (z.state == 2 ? (z.wasValid ? "I.VS" : "SBR") : (z.srr ? "RSS" : (z.state == 1 ? "V.R" : "R")));
   if(z.dead)
      base += " x";
   else if(z.touches > 0)
      base += " T" + IntegerToString(z.touches);
   return base;
  }

void DrawZone(const SZone &z, const datetime t1, const datetime t2)
  {
   string nm = ZoneName(z.id);
   if(ObjectFind(0, nm) < 0)
     {
      ObjectCreate(0, nm, OBJ_RECTANGLE, 0, t1, z.top, t2, z.bot);
      ObjectSetInteger(0, nm, OBJPROP_FILL, true);
      ObjectSetInteger(0, nm, OBJPROP_BACK, true);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, nm, OBJPROP_TIME, 0, t1);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 0, z.top);
   ObjectSetInteger(0, nm, OBJPROP_TIME, 1, t2);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 1, z.bot);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, ZoneColor(z));

   string ln = ZoneLblName(z.id);
   if(ObjectFind(0, ln) < 0)
     {
      ObjectCreate(0, ln, OBJ_TEXT, 0, t2, (z.top + z.bot) / 2.0);
      ObjectSetInteger(0, ln, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, ln, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, ln, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, ln, OBJPROP_ANCHOR, ANCHOR_LEFT);
     }
   ObjectSetInteger(0, ln, OBJPROP_TIME, 0, t2);
   ObjectSetDouble (0, ln, OBJPROP_PRICE, 0, (z.top + z.bot) / 2.0);
   ObjectSetString (0, ln, OBJPROP_TEXT, ZoneText(z));
   ObjectSetInteger(0, ln, OBJPROP_COLOR, ZoneColor(z));
  }

void DeleteZone(const SZone &z)
  {
   ObjectDelete(0, ZoneName(z.id));
   ObjectDelete(0, ZoneLblName(z.id));
  }

void RemoveZoneAt(const int idx)
  {
   int n = ArraySize(g_zones);
   if(idx < 0 || idx >= n)
      return;
   DeleteZone(g_zones[idx]);
   for(int i = idx; i < n - 1; i++)
      g_zones[i] = g_zones[i + 1];
   ArrayResize(g_zones, n - 1);
  }
//+------------------------------------------------------------------+
//| Zone overlap check                                                |
//+------------------------------------------------------------------+
// an exhausted (dead) zone must not keep the area reserved forever — once a
// zone has had its touches the book says you redraw it
bool Overlaps(const double top, const double bot)
  {
   for(int i = 0; i < ArraySize(g_zones); i++)
      if(!g_zones[i].dead && !(bot > g_zones[i].top || top < g_zones[i].bot))
         return true;
   return false;
  }
//+------------------------------------------------------------------+
//| Add zone (with SNRZ min/max height clamps)                        |
//+------------------------------------------------------------------+
void AddZone(double top, double bot, const int role, const int bornH, const double atr,
             const datetime t1, const datetime t2)
  {
   double h  = top - bot;
   double mn = atr * InpMinZoneATR;
   double mx = atr * InpMaxZoneATR;
   if(h < mn)
     {
      double mid = (top + bot) / 2.0;
      top = mid + mn / 2.0;
      bot = mid - mn / 2.0;
     }
   if(top - bot > mx)
     {
      if(role == 1) top = bot + mx;
      else          bot = top - mx;
     }
   int n = ArraySize(g_zones);
   ArrayResize(g_zones, n + 1);
   g_zones[n].top        = top;
   g_zones[n].bot        = bot;
   g_zones[n].role       = role;
   g_zones[n].state      = 0;
   g_zones[n].touches    = 0;
   g_zones[n].oppBreaks  = 0;
   g_zones[n].srr        = false;
   g_zones[n].wasValid   = false;
   g_zones[n].dead       = false;
   g_zones[n].sigTouch   = 0;
   g_zones[n].bornH      = bornH;
   g_zones[n].bornTime   = t1;
   g_zones[n].activeFrom = t2;
   g_zones[n].id         = ++g_zoneSeq;
   g_zones[n].inZonePrev = false;
   DrawZone(g_zones[n], t1, t2);

   while(ArraySize(g_zones) > InpMaxZones)
     {
      int victim = 0;                      // evict an exhausted zone first
      for(int i = 0; i < ArraySize(g_zones); i++)
         if(g_zones[i].dead)
           {
            victim = i;
            break;
           }
      RemoveZoneAt(victim);
     }
  }
//+------------------------------------------------------------------+
//| 75% breakout rule                                                 |
//+------------------------------------------------------------------+
bool BullBreak75(const double lvl, const double o, const double h, const double l, const double c)
  {
   if(c > lvl && o > lvl)              // full body above
      return true;
   if(c <= lvl)
      return false;
   double bodyLow = MathMin(o, c);
   double len = h - bodyLow;           // ignore lower shadow
   if(len <= 0.0)
      return false;
   double outside = h - MathMax(lvl, bodyLow);
   return (outside / len * 100.0) >= InpBreakoutPct;
  }

bool BearBreak75(const double lvl, const double o, const double h, const double l, const double c)
  {
   if(c < lvl && o < lvl)
      return true;
   if(c >= lvl)
      return false;
   double bodyHigh = MathMax(o, c);
   double len = bodyHigh - l;          // ignore upper shadow
   if(len <= 0.0)
      return false;
   double outside = MathMin(lvl, bodyHigh) - l;
   return (outside / len * 100.0) >= InpBreakoutPct;
  }
//+------------------------------------------------------------------+
//| Alert helper                                                      |
//+------------------------------------------------------------------+
void Notify(const string msg, const bool live)
  {
   if(!live)                       // never alert while loading history
      return;
   string full = "SNRZ " + _Symbol + " " + EnumToString((ENUM_TIMEFRAMES)_Period) + ": " + msg;
   if(InpAlertPopup) Alert(full);
   if(InpAlertPush)  SendNotification(full);
  }
//+------------------------------------------------------------------+
//| Position drawing (last setup stays visible, faded when closed)    |
//+------------------------------------------------------------------+
void PosLine(const string tag, const datetime t1, const datetime t2,
             const double price, const color col, const int width, const int style)
  {
   string nm = g_prefix + "P_" + tag;
   if(ObjectFind(0, nm) < 0)
     {
      ObjectCreate(0, nm, OBJ_TREND, 0, t1, price, t2, price);
      ObjectSetInteger(0, nm, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, nm, OBJPROP_TIME,  0, t1);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 0, price);
   ObjectSetInteger(0, nm, OBJPROP_TIME,  1, t2);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 1, price);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
   ObjectSetInteger(0, nm, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, nm, OBJPROP_STYLE, style);
  }

void PosText(const string tag, const datetime t, const double price,
             const string txt, const color col)
  {
   string nm = g_prefix + "T_" + tag;
   if(ObjectFind(0, nm) < 0)
     {
      ObjectCreate(0, nm, OBJ_TEXT, 0, t, price);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, nm, OBJPROP_FONTSIZE, 9);
      ObjectSetInteger(0, nm, OBJPROP_ANCHOR, ANCHOR_LEFT);
     }
   ObjectSetInteger(0, nm, OBJPROP_TIME,  0, t);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 0, price);
   ObjectSetString (0, nm, OBJPROP_TEXT, txt);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
  }

void DrawPosition(const datetime tNow)
  {
   if(!InpShowPosition || g_posTime == 0)
      return;
   datetime t2 = tNow + (datetime)(PeriodSeconds(_Period) * 25);
   color dirCol = g_posBuy ? clrMediumSeaGreen : clrTomato;
   color head   = g_posPO2 ? InpColInv : dirCol;
   int   st     = g_posOn ? STYLE_SOLID : STYLE_DOT;
   string kind  = g_posSwing ? "SWING" : "SCALP";
   string dir   = g_posBuy ? "BUY" : "SELL";
   string stat  = g_posStat == -1 ? "  x SL" :
                  g_posStat == 3  ? "  v TP3" :
                  g_posStat == 2  ? "  v TP2" :
                  g_posStat == 1  ? "  v TP1" : (g_posOn ? "  . running" : "  . closed");

   PosLine("E",  g_posTime, t2, g_posEntry, clrWhite,           2, STYLE_DOT);
   PosLine("SL", g_posTime, t2, g_posSL,    clrTomato,          2, st);
   PosLine("T1", g_posTime, t2, g_posTP1,   clrMediumSeaGreen,  2, st);
   // on a FINISHED trade only the levels it actually reached stay drawn
   if(g_posOn || g_posStat >= 2)
      PosLine("T2", g_posTime, t2, g_posTP2, clrMediumSeaGreen, 1, STYLE_DASH);
   else
     {
      ObjectDelete(0, g_prefix + "P_T2");
      ObjectDelete(0, g_prefix + "T_T2");
     }
   if(g_posOn || g_posStat >= 3)
      PosLine("T3", g_posTime, t2, g_posTP3, clrMediumSeaGreen, 1, STYLE_DASH);
   else
     {
      ObjectDelete(0, g_prefix + "P_T3");
      ObjectDelete(0, g_prefix + "T_T3");
     }

   PosText("E",  t2, g_posEntry, dir + " " + kind + (g_posPO2 ? " PO2" : "") + " " +
           g_posZone + stat + "  " + DoubleToString(g_posEntry, _Digits), head);
   PosText("SL", t2, g_posSL,  "SL  "  + DoubleToString(g_posSL,  _Digits), clrTomato);
   PosText("T1", t2, g_posTP1, "TP1 " + DoubleToString(g_posTP1, _Digits), clrMediumSeaGreen);
   if(g_posOn || g_posStat >= 2)
      PosText("T2", t2, g_posTP2, "TP2 " + DoubleToString(g_posTP2, _Digits), clrMediumSeaGreen);
   if(g_posOn || g_posStat >= 3)
      PosText("T3", t2, g_posTP3, "TP3 " + DoubleToString(g_posTP3, _Digits), clrMediumSeaGreen);
  }
//+------------------------------------------------------------------+
//| Targets: nearest opposite zones ahead of price, else 1R/2R/3R     |
//+------------------------------------------------------------------+
void BuildTargets(const bool isBuy, const double entry, const double risk,
                  double &t1, double &t2, double &t3)
  {
   double dists[];
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].dead)
         continue;
      double lvl = isBuy ? g_zones[i].bot : g_zones[i].top;
      bool ahead = isBuy ? (g_zones[i].role == -1 && lvl > entry)
                   : (g_zones[i].role ==  1 && lvl < entry);
      if(!ahead)
         continue;
      int n = ArraySize(dists);
      ArrayResize(dists, n + 1);
      dists[n] = MathAbs(lvl - entry);
     }
   if(ArraySize(dists) > 1)
      ArraySort(dists);

   // The book takes TP1 at the NEAREST liquidity. A zone sitting 15R away is a
   // destination, not a first target — only zones within InpTpMaxR feed
   // TP1/TP2; anything beyond that can still serve as TP3.
   double cap = risk * InpTpMaxR;
   double nearD[], farD[];
   for(int i = 0; i < ArraySize(dists); i++)
     {
      if(dists[i] <= cap)
        {
         int k = ArraySize(nearD);
         ArrayResize(nearD, k + 1);
         nearD[k] = dists[i];
        }
      else
        {
         int k = ArraySize(farD);
         ArrayResize(farD, k + 1);
         farD[k] = dists[i];
        }
     }

   int    n  = ArraySize(nearD);
   int    nf = ArraySize(farD);
   double d1 = n >= 1 ? nearD[0] : risk;
   double d2 = n >= 2 ? nearD[1] : MathMax(d1 + risk, risk * 2.0);
   double d3 = n >= 3 ? nearD[2] : (nf > 0 ? farD[0] : MathMax(d2 + risk, risk * 3.0));
   // book: RR at least 1:1, and each target beyond the previous one
   d1 = MathMax(d1, risk);
   d2 = MathMax(d2, d1 + risk * 0.5);
   d3 = MathMax(d3, d2 + risk * 0.5);
   t1 = isBuy ? entry + d1 : entry - d1;
   t2 = isBuy ? entry + d2 : entry - d2;
   t3 = isBuy ? entry + d3 : entry - d3;
  }
//+------------------------------------------------------------------+
//| Process one CLOSED analysis-timeframe bar: pivots, zones, trend   |
//+------------------------------------------------------------------+
void ProcessHtfBar(const int j, const MqlRates &htf[], const double &atrH[], const int hCount)
  {
   double atr = atrH[j];
   if(atr <= 0.0)
      return;

   int p = j - InpPivotLen;                  // pivot candidate
   if(p >= InpPivotLen)
     {
      bool isPH = true, isPL = true;
      for(int k = p - InpPivotLen; k <= p + InpPivotLen; k++)
        {
         if(k == p) continue;
         if(htf[k].high >= htf[p].high) isPH = false;
         if(htf[k].low  <= htf[p].low)  isPL = false;
         if(!isPH && !isPL) break;
        }
      if(isPH) { g_prevHigh = g_lastHigh; g_lastHigh = htf[p].high; }
      if(isPL) { g_prevLow  = g_lastLow;  g_lastLow  = htf[p].low;  }

      // the movement made AFTER the pivot is the book's "Big Movement"
      double hiRun = htf[p].high, loRun = htf[p].low;
      for(int k = p; k <= j; k++)
        {
         hiRun = MathMax(hiRun, htf[k].high);
         loRun = MathMin(loRun, htf[k].low);
        }
      double bigMove = atr * InpBigMoveATR;

      if(isPL)
        {
         double zTop = MathMin(htf[p].open, htf[p].close);
         double zBot = htf[p].low;
         if((hiRun - zBot) >= bigMove && !Overlaps(zTop, zBot))
            AddZone(zTop, zBot, 1, j, atr, htf[p].time, htf[j].time);
        }
      if(isPH)
        {
         double zTop = htf[p].high;
         double zBot = MathMax(htf[p].open, htf[p].close);
         if((zTop - loRun) >= bigMove && !Overlaps(zTop, zBot))
            AddZone(zTop, zBot, -1, j, atr, htf[p].time, htf[j].time);
        }
     }

   // Book: a close beyond the last confirmed swing IS the Break of Structure,
   // and that is what turns the trend. Only the moment it breaks counts.
   double hc = htf[j].close;
   bool bosUp = (g_lastHigh > 0 && hc > g_lastHigh + atr * 0.1);
   bool bosDn = (g_lastLow  > 0 && hc < g_lastLow  - atr * 0.1);
   if(bosUp && !g_bosUpPrev) { g_lastBosUpH = j; g_trendState = 1; }
   if(bosDn && !g_bosDnPrev) { g_lastBosDnH = j; g_trendState = -1; }
   g_bosUpPrev = bosUp;
   g_bosDnPrev = bosDn;
   if(g_trendState == 0 && g_prevHigh > 0 && g_prevLow > 0)
     {
      if(g_lastHigh > g_prevHigh && g_lastLow > g_prevLow)      g_trendState = 1;
      else if(g_lastHigh < g_prevHigh && g_lastLow < g_prevLow) g_trendState = -1;
     }

   // expire zones by analysis-TF age
   for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
      if(j - g_zones[i].bornH > InpZoneMaxAge)
         RemoveZoneAt(i);
  }
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   if(rates_total < InpPivotLen * 2 + 20)
      return 0;

   //--- analysis-timeframe series -------------------------------------------
   double tfRatio = (double)PeriodSeconds(g_atf) / (double)PeriodSeconds(_Period);
   int need = (int)MathMin(5000.0, rates_total / MathMax(tfRatio, 1.0) + InpPivotLen * 4 + 60);
   need = MathMax(need, InpPivotLen * 4 + 60);

   static MqlRates htf[];
   int hCount = CopyRates(_Symbol, g_atf, 0, need, htf);
   if(hCount <= InpPivotLen * 2 + 5)
      return prev_calculated;               // higher timeframe not loaded yet
   ArraySetAsSeries(htf, false);

   static double atrH[];
   if(CopyBuffer(g_atrHtfHandle, 0, 0, hCount, atrH) <= 0)
      return prev_calculated;
   ArraySetAsSeries(atrH, false);

   static double atrBuf[];
   if(CopyBuffer(g_atrHandle, 0, 0, rates_total, atrBuf) <= 0)
      return prev_calculated;
   ArraySetAsSeries(atrBuf, false);

   static int lastProcessed = -1;
   static int hIdx = -1;

   if(prev_calculated == 0)                 // full recalculation -> clean slate
     {
      ArrayInitialize(BufBuy,     EMPTY_VALUE);
      ArrayInitialize(BufSell,    EMPTY_VALUE);
      ArrayInitialize(BufPO2Buy,  EMPTY_VALUE);
      ArrayInitialize(BufPO2Sell, EMPTY_VALUE);
      ObjectsDeleteAll(0, g_prefix);
      ArrayResize(g_zones, 0);
      lastProcessed = -1;
      hIdx = -1;
      g_lastHigh = g_prevHigh = g_lastLow = g_prevLow = 0;
      g_trendState = 0;
      g_lastBosUpH = g_lastBosDnH = -999999;
      g_bosUpPrev = g_bosDnPrev = false;
      g_posOn = false; g_posTime = 0; g_posStat = 0;
     }

   int start = MathMax(prev_calculated - 1, InpPivotLen * 2 + 15);

   for(int bar = start; bar < rates_total - 1; bar++)   // closed bars only
     {
      if(bar <= lastProcessed)
         continue;
      lastProcessed = bar;

      BufBuy[bar] = EMPTY_VALUE;  BufSell[bar]    = EMPTY_VALUE;
      BufPO2Buy[bar] = EMPTY_VALUE; BufPO2Sell[bar] = EMPTY_VALUE;
      if(bar + 1 < rates_total)
        {
         BufBuy[bar + 1] = EMPTY_VALUE;  BufSell[bar + 1]    = EMPTY_VALUE;
         BufPO2Buy[bar + 1] = EMPTY_VALUE; BufPO2Sell[bar + 1] = EMPTY_VALUE;
        }

      double atr = atrBuf[bar];
      if(atr <= 0.0)
         continue;
      bool live = (bar >= rates_total - 2);   // only alert on the latest closed bar

      //--- advance the analysis-timeframe cursor (merge join, no repaint) ----
      if(hIdx < 0)
        {
         // find the first analysis bar that is already closed at this chart bar
         int j = 0;
         while(j + 1 < hCount && htf[j + 1].time <= time[bar])
            j++;
         if(j < InpPivotLen * 2 + 1)
            continue;                        // not enough history yet
         hIdx = j;
        }
      while(hIdx + 1 < hCount && htf[hIdx + 1].time <= time[bar])
        {
         hIdx++;
         ProcessHtfBar(hIdx, htf, atrH, hCount);
        }

      double atrA = atrH[hIdx] > 0.0 ? atrH[hIdx] : atr;

      // both sides of structure broken recently = sideway. "No Setup, No Trade".
      bool inRange   = (hIdx - g_lastBosUpH) <= InpRangeBars &&
                       (hIdx - g_lastBosDnH) <= InpRangeBars;
      bool trendUp   = (g_trendState ==  1 && !inRange);
      bool trendDown = (g_trendState == -1 && !inRange);

      //--- confirmation candles (SNRZ style, on the CHART timeframe) --------
      double o = open[bar], h = high[bar], l = low[bar], c = close[bar];
      double o1 = open[bar - 1], c1 = close[bar - 1];
      bool bullEngulf = (c > o) && (c1 < o1) && (c >= o1);
      bool bearEngulf = (c < o) && (c1 > o1) && (c <= o1);
      double rng = h - l;
      bool bullPin = rng > 0 && (MathMin(o, c) - l) >= 0.6 * rng && c >= o;
      bool bearPin = rng > 0 && (h - MathMax(o, c)) >= 0.6 * rng && c <= o;
      bool bullConfirm = bullEngulf || bullPin;
      bool bearConfirm = bearEngulf || bearPin;
      bool brokeSupportNow    = false;
      bool brokeResistanceNow = false;

      //--- resolve the open position ---------------------------------------
      if(g_posOn)
        {
         if(g_posBuy)
           {
            if(l <= g_posSL)                          { g_posStat = -1; g_posOn = false; }
            else if(h >= g_posTP3)                    { g_posStat =  3; g_posOn = false; }
            else if(h >= g_posTP2 && g_posStat < 2)     g_posStat =  2;
            else if(h >= g_posTP1 && g_posStat < 1)     g_posStat =  1;
           }
         else
           {
            if(h >= g_posSL)                          { g_posStat = -1; g_posOn = false; }
            else if(l <= g_posTP3)                    { g_posStat =  3; g_posOn = false; }
            else if(l <= g_posTP2 && g_posStat < 2)     g_posStat =  2;
            else if(l <= g_posTP1 && g_posStat < 1)     g_posStat =  1;
           }
         if(g_posOn && bar - g_posBar > InpMaxTradeBars)
            g_posOn = false;                 // never block the next setup forever
        }

      // A Weekly/Daily zone can be years old and hundreds of points away. It is
      // not tradeable any more and it wrecks the chart scale, so drop it.
      double maxZoneDist = atrA * InpMaxZoneDistATR;
      for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
        {
         double gap = c > g_zones[i].top ? c - g_zones[i].top
                      : (c < g_zones[i].bot ? g_zones[i].bot - c : 0.0);
         if(gap > maxZoneDist)
            RemoveZoneAt(i);
        }

      // book: don't overtrade — manage one setup at a time
      bool canFire  = !(InpOneTrade && g_posOn);
      bool sigFired = false;

      //--- zone engine -----------------------------------------------------
      for(int i = 0; i < ArraySize(g_zones); i++)
        {
         if(time[bar] <= g_zones[i].activeFrom)
            continue;
         bool inZone = (l <= g_zones[i].top && h >= g_zones[i].bot);

         if(g_zones[i].role == 1)   // SUPPORT
           {
            if(BearBreak75(g_zones[i].bot, o, h, l, c))
              {
               g_zones[i].wasValid = (g_zones[i].state == 1);
               g_zones[i].role  = -1;
               g_zones[i].state = 2;
               g_zones[i].touches  = 0;
               g_zones[i].sigTouch = 0;
               g_zones[i].srr      = false;
               g_zones[i].dead     = false;
               brokeSupportNow     = true;
               Notify((g_zones[i].wasValid ? "I.VS" : "SBR") + " — Support broken (75% rule), zone inverted to SELL", live);
              }
            else if(inZone && c >= g_zones[i].bot && !g_zones[i].dead)
              {
               if(!g_zones[i].inZonePrev)
                 {
                  g_zones[i].touches++;
                  if(g_zones[i].state == 0 && g_zones[i].touches >= 2)
                     g_zones[i].state = 1;   // Second Movement → VALID
                  if((g_zones[i].state != 2 && g_zones[i].touches > InpMaxTouches) ||
                     (g_zones[i].state == 2 && g_zones[i].touches > 2))
                     g_zones[i].dead = true; // 3-touch rule: zone exhausted
                 }
               // tradable only: VALID (touch>=2), SRR, or INVERSION (touch 1-2)
               bool tradable = !g_zones[i].dead &&
                               ((g_zones[i].state == 1 && g_zones[i].touches >= 2) ||
                                (g_zones[i].srr && g_zones[i].touches >= 1) ||
                                (g_zones[i].state == 2 && g_zones[i].touches >= 1 && g_zones[i].touches <= 2));
               bool okTrend = !InpTrendFilter || trendUp ||
                              (InpAllowCounterInv && g_zones[i].state == 2);
               bool okConf  = !InpNeedConfirm || bullConfirm;
               bool fresh   = (g_zones[i].sigTouch != g_zones[i].touches);
               bool rejectOK = InpNeedReject ? (c > g_zones[i].top) : (c > g_zones[i].bot);
               if(tradable && okTrend && okConf && fresh && rejectOK && canFire && !sigFired)
                 {
                  g_zones[i].sigTouch = g_zones[i].touches;
                  bool isPO2 = (g_zones[i].state == 2 && g_zones[i].touches == 2);
                  sigFired = true;
                  if(isPO2)
                    {
                     BufPO2Buy[bar] = l - atr * 0.4;
                     Notify("PO2 BUY — Power of Second Touch at " + ZoneText(g_zones[i]), live);
                    }
                  else
                    {
                     BufBuy[bar] = l - atr * 0.3;
                     Notify("BUY — rejection at " + ZoneText(g_zones[i]), live);
                    }
                  // open the position
                  double swingLo = MathMin(MathMin(low[bar], low[bar - 1]), low[bar - 2]);
                  double rawSl   = MathMin(g_zones[i].bot, swingLo) - atrA * 0.15;
                  double risk    = MathMax(MathAbs(c - rawSl), atrA * InpMinSlATR);
                  double t1, t2, t3;
                  BuildTargets(true, c, risk, t1, t2, t3);
                  g_posOn = true; g_posBuy = true; g_posPO2 = isPO2;
                  g_posEntry = c; g_posSL = c - risk;
                  g_posTP1 = t1; g_posTP2 = t2; g_posTP3 = t3;
                  g_posSwing = (PeriodSeconds(_Period) >= 3600);
                  g_posBar = bar; g_posTime = time[bar];
                  g_posZone = ZoneText(g_zones[i]); g_posStat = 0;
                 }
              }
           }
         else                        // RESISTANCE
           {
            if(BullBreak75(g_zones[i].top, o, h, l, c))
              {
               g_zones[i].wasValid = (g_zones[i].state == 1);
               g_zones[i].role  = 1;
               g_zones[i].state = 2;
               g_zones[i].touches  = 0;
               g_zones[i].sigTouch = 0;
               g_zones[i].srr      = false;
               g_zones[i].dead     = false;
               brokeResistanceNow  = true;
               Notify((g_zones[i].wasValid ? "I.VR" : "RBS") + " — Resistance broken (75% rule), zone inverted to BUY", live);
              }
            else if(inZone && c <= g_zones[i].top && !g_zones[i].dead)
              {
               if(!g_zones[i].inZonePrev)
                 {
                  g_zones[i].touches++;
                  if(g_zones[i].state == 0 && g_zones[i].touches >= 2)
                     g_zones[i].state = 1;
                  if((g_zones[i].state != 2 && g_zones[i].touches > InpMaxTouches) ||
                     (g_zones[i].state == 2 && g_zones[i].touches > 2))
                     g_zones[i].dead = true;
                 }
               bool tradable = !g_zones[i].dead &&
                               ((g_zones[i].state == 1 && g_zones[i].touches >= 2) ||
                                (g_zones[i].srr && g_zones[i].touches >= 1) ||
                                (g_zones[i].state == 2 && g_zones[i].touches >= 1 && g_zones[i].touches <= 2));
               bool okTrend = !InpTrendFilter || trendDown ||
                              (InpAllowCounterInv && g_zones[i].state == 2);
               bool okConf  = !InpNeedConfirm || bearConfirm;
               bool fresh   = (g_zones[i].sigTouch != g_zones[i].touches);
               bool rejectOK = InpNeedReject ? (c < g_zones[i].bot) : (c < g_zones[i].top);
               if(tradable && okTrend && okConf && fresh && rejectOK && canFire && !sigFired)
                 {
                  g_zones[i].sigTouch = g_zones[i].touches;
                  bool isPO2 = (g_zones[i].state == 2 && g_zones[i].touches == 2);
                  sigFired = true;
                  if(isPO2)
                    {
                     BufPO2Sell[bar] = h + atr * 0.4;
                     Notify("PO2 SELL — Power of Second Touch at " + ZoneText(g_zones[i]), live);
                    }
                  else
                    {
                     BufSell[bar] = h + atr * 0.3;
                     Notify("SELL — rejection at " + ZoneText(g_zones[i]), live);
                    }
                  double swingHi = MathMax(MathMax(high[bar], high[bar - 1]), high[bar - 2]);
                  double rawSl   = MathMax(g_zones[i].top, swingHi) + atrA * 0.15;
                  double risk    = MathMax(MathAbs(rawSl - c), atrA * InpMinSlATR);
                  double t1, t2, t3;
                  BuildTargets(false, c, risk, t1, t2, t3);
                  g_posOn = true; g_posBuy = false; g_posPO2 = isPO2;
                  g_posEntry = c; g_posSL = c + risk;
                  g_posTP1 = t1; g_posTP2 = t2; g_posTP3 = t3;
                  g_posSwing = (PeriodSeconds(_Period) >= 3600);
                  g_posBar = bar; g_posTime = time[bar];
                  g_posZone = ZoneText(g_zones[i]); g_posStat = 0;
                 }
              }
           }
         g_zones[i].inZonePrev = inZone;
         DrawZone(g_zones[i], g_zones[i].bornTime, time[bar]);
        }

      //--- SRR / RSS qualification (book): Support that broke >=2 Resistances
      //    becomes SRR (buy); Resistance that broke >=2 Supports becomes RSS.
      if(brokeSupportNow || brokeResistanceNow)
        {
         for(int i = 0; i < ArraySize(g_zones); i++)
           {
            if(g_zones[i].state == 2 || g_zones[i].dead)
               continue;
            if(brokeResistanceNow && g_zones[i].role == 1 && g_zones[i].touches == 0 && c > g_zones[i].top)
              {
               g_zones[i].oppBreaks++;
               if(g_zones[i].oppBreaks >= 2)
                  g_zones[i].srr = true;
              }
            if(brokeSupportNow && g_zones[i].role == -1 && g_zones[i].touches == 0 && c < g_zones[i].bot)
              {
               g_zones[i].oppBreaks++;
               if(g_zones[i].oppBreaks >= 2)
                  g_zones[i].srr = true;
              }
           }
        }
     }

   DrawPosition(time[rates_total - 1]);
   ChartRedraw();
   return rates_total;
  }
//+------------------------------------------------------------------+
