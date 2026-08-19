//+------------------------------------------------------------------+
//|                                               SNRZ_Indicator.mq5 |
//|        SNRZ Strategy — "Zindan The Gold Chaser" Masterclass      |
//|                                                                  |
//|  Implements on MetaTrader 5:                                     |
//|   • Valid Support / Valid Resistance (two-movement rule)         |
//|   • 75% breakout rule (body + directional shadow only)           |
//|   • Inversion: RBS / SBR → IVS / IVR when a VALID zone breaks    |
//|   • Power of Second Touch (PO2) — strongest entry                |
//|   • SNRZ engulfing / pin-bar confirmation                        |
//|   • Structure trend filter (HH/HL vs LH/LL)                      |
//|   • Chart zones as rectangles + arrows + Alert / Push            |
//+------------------------------------------------------------------+
#property copyright   "SNRZ (Zindan The Gold Chaser) — indicator port"
#property version     "1.00"
#property description "SNRZ zones: Valid S/R, Inversion (RBS/SBR/IVS/IVR), PO2, 75% breakout rule"
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
input int    InpPivotLen     = 8;     // Pivot length (swing sensitivity)
input int    InpMaxZones     = 10;    // Max active zones
input double InpBigMoveATR   = 1.5;   // "Big Movement" >= ATR x
input double InpBreakoutPct  = 75.0;  // Breakout rule (%) — the 75% rule
input double InpMinZoneATR   = 0.15;  // Min zone height (ATR x)
input double InpMaxZoneATR   = 1.60;  // Max zone height (ATR x)

input group "Signals"
input bool   InpTrendFilter  = true;  // Trade only with structure trend
input bool   InpNeedConfirm  = true;  // Require confirmation candle
input int    InpMaxTouches   = 3;     // Max touches per zone (3-touch rule)

input group "Alerts"
input bool   InpAlertPopup   = true;  // Alert window
input bool   InpAlertPush    = false; // Push notification to phone

input group "Style"
input color  InpColSup       = C'8,153,129';   // Support zone
input color  InpColRes       = C'242,54,69';   // Resistance zone
input color  InpColInv       = C'255,152,0';   // Inversion zone
input uchar  InpFillAlpha    = 40;             // (reserved)

//--- buffers ----------------------------------------------------------------
double BufBuy[], BufSell[], BufPO2Buy[], BufPO2Sell[];

//--- zone storage -----------------------------------------------------------
struct SZone
  {
   double top;
   double bot;
   int    role;      // 1 = Support, -1 = Resistance
   int    state;     // 0 fresh, 1 VALID, 2 inverted
   int    touches;
   bool   wasValid;
   bool   dead;      // 3-touch rule exhausted -> no more trades
   int    sigTouch;  // anti-spam latch: one signal per touch
   int    bornBar;
   long   id;        // object id
   bool   inZonePrev;
  };
SZone  g_zones[];
long   g_zoneSeq = 0;

int    g_atrHandle = INVALID_HANDLE;
string g_prefix    = "SNRZ_";

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

   g_atrHandle = iATR(_Symbol, _Period, 14);
   if(g_atrHandle == INVALID_HANDLE)
      return INIT_FAILED;

   IndicatorSetString(INDICATOR_SHORTNAME, "SNRZ [Zindan]");
   return INIT_SUCCEEDED;
  }
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ObjectsDeleteAll(0, g_prefix);
   if(g_atrHandle != INVALID_HANDLE)
      IndicatorRelease(g_atrHandle);
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
      base = (z.state == 2 ? (z.wasValid ? "IVS" : "RBS") : (z.state == 1 ? "V.S" : "S"));
   else
      base = (z.state == 2 ? (z.wasValid ? "IVR" : "SBR") : (z.state == 1 ? "V.R" : "R"));
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
//+------------------------------------------------------------------+
//| Zone overlap check                                                |
//+------------------------------------------------------------------+
bool Overlaps(const double top, const double bot)
  {
   for(int i = 0; i < ArraySize(g_zones); i++)
      if(!(bot > g_zones[i].top || top < g_zones[i].bot))
         return true;
   return false;
  }
//+------------------------------------------------------------------+
//| Add zone (with SNRZ min/max height clamps)                        |
//+------------------------------------------------------------------+
void AddZone(double top, double bot, const int role, const int bornBar, const double atr,
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
   g_zones[n].wasValid   = false;
   g_zones[n].dead       = false;
   g_zones[n].sigTouch   = 0;
   g_zones[n].bornBar    = bornBar;
   g_zones[n].id         = ++g_zoneSeq;
   g_zones[n].inZonePrev = false;
   DrawZone(g_zones[n], t1, t2);

   // keep only newest InpMaxZones
   while(ArraySize(g_zones) > InpMaxZones)
     {
      DeleteZone(g_zones[0]);
      for(int i = 0; i < ArraySize(g_zones) - 1; i++)
         g_zones[i] = g_zones[i + 1];
      ArrayResize(g_zones, ArraySize(g_zones) - 1);
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

   if(prev_calculated == 0)
     {
      ArrayInitialize(BufBuy,     EMPTY_VALUE);
      ArrayInitialize(BufSell,    EMPTY_VALUE);
      ArrayInitialize(BufPO2Buy,  EMPTY_VALUE);
      ArrayInitialize(BufPO2Sell, EMPTY_VALUE);
     }

   // ATR values (non-series alignment)
   static double atrBuf[];
   if(CopyBuffer(g_atrHandle, 0, 0, rates_total, atrBuf) <= 0)
      return prev_calculated;
   ArraySetAsSeries(atrBuf, false);

   int start = MathMax(prev_calculated - 1, InpPivotLen * 2 + 15);

   // structure trend state persists across calls
   static double lastHigh = 0, prevHigh = 0, lastLow = 0, prevLow = 0;
   static int    lastProcessed = -1;

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
      double bigMove = atr * InpBigMoveATR;
      bool   live    = (bar >= rates_total - 2);   // only alert on the latest closed bar

      //--- pivot detection at bar - InpPivotLen ---------------------------
      int p = bar - InpPivotLen;
      if(p >= InpPivotLen)
        {
         bool isPH = true, isPL = true;
         for(int k = p - InpPivotLen; k <= p + InpPivotLen; k++)
           {
            if(k == p) continue;
            if(high[k] >= high[p]) isPH = false;
            if(low[k]  <= low[p])  isPL = false;
            if(!isPH && !isPL) break;
           }
         // update structure trend
         if(isPH) { prevHigh = lastHigh; lastHigh = high[p]; }
         if(isPL) { prevLow  = lastLow;  lastLow  = low[p];  }

         datetime t1 = time[p];
         datetime t2 = time[bar];
         if(isPL)
           {
            double zTop = MathMin(open[p], close[p]);
            double zBot = low[p];
            if((high[bar] - zBot) >= bigMove && !Overlaps(zTop, zBot))
               AddZone(zTop, zBot, 1, bar, atr, t1, t2);
           }
         if(isPH)
           {
            double zTop = high[p];
            double zBot = MathMax(open[p], close[p]);
            if((zTop - low[bar]) >= bigMove && !Overlaps(zTop, zBot))
               AddZone(zTop, zBot, -1, bar, atr, t1, t2);
           }
        }

      bool trendUp   = (lastHigh > prevHigh && lastLow > prevLow && prevHigh > 0 && prevLow > 0);
      bool trendDown = (lastHigh < prevHigh && lastLow < prevLow && prevHigh > 0 && prevLow > 0);

      //--- confirmation candles (SNRZ style) ------------------------------
      double o = open[bar], h = high[bar], l = low[bar], c = close[bar];
      double o1 = open[bar - 1], c1 = close[bar - 1];
      bool bullEngulf = (c > o) && (c1 < o1) && (c >= o1);
      bool bearEngulf = (c < o) && (c1 > o1) && (c <= o1);
      double rng = h - l;
      bool bullPin = rng > 0 && (MathMin(o, c) - l) >= 0.6 * rng && c >= o;
      bool bearPin = rng > 0 && (h - MathMax(o, c)) >= 0.6 * rng && c <= o;
      bool bullConfirm = bullEngulf || bullPin;
      bool bearConfirm = bearEngulf || bearPin;

      //--- zone engine -----------------------------------------------------
      for(int i = 0; i < ArraySize(g_zones); i++)
        {
         if(bar <= g_zones[i].bornBar)
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
               g_zones[i].dead     = false;
               Notify((g_zones[i].wasValid ? "IVR" : "SBR") + " — Support broken (75% rule), zone inverted to SELL", live);
              }
            else if(inZone && c >= g_zones[i].bot && !g_zones[i].dead)
              {
               if(!g_zones[i].inZonePrev)
                 {
                  g_zones[i].touches++;
                  if(g_zones[i].state == 0 && g_zones[i].touches >= 2)
                     g_zones[i].state = 1;   // Second Movement → VALID
                  if((g_zones[i].state == 1 && g_zones[i].touches > InpMaxTouches) ||
                     (g_zones[i].state == 2 && g_zones[i].touches > 2))
                     g_zones[i].dead = true; // 3-touch rule: zone exhausted
                 }
               // tradable only: VALID (touch>=2) or INVERSION (touch 1-2)
               bool tradable = !g_zones[i].dead &&
                               ((g_zones[i].state == 1 && g_zones[i].touches >= 2) ||
                                (g_zones[i].state == 2 && g_zones[i].touches >= 1 && g_zones[i].touches <= 2));
               bool okTrend = !InpTrendFilter || trendUp || g_zones[i].state == 2;
               bool okConf  = !InpNeedConfirm || bullConfirm;
               bool fresh   = (g_zones[i].sigTouch != g_zones[i].touches);
               if(tradable && okTrend && okConf && fresh && c > g_zones[i].bot)
                 {
                  g_zones[i].sigTouch = g_zones[i].touches;
                  if(g_zones[i].touches == 2)
                    {
                     BufPO2Buy[bar] = l - atr * 0.4;
                     Notify("PO2 BUY — Power of Second Touch at " + ZoneText(g_zones[i]), live);
                    }
                  else
                    {
                     BufBuy[bar] = l - atr * 0.3;
                     Notify("BUY — rejection at " + ZoneText(g_zones[i]), live);
                    }
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
               g_zones[i].dead     = false;
               Notify((g_zones[i].wasValid ? "IVS" : "RBS") + " — Resistance broken (75% rule), zone inverted to BUY", live);
              }
            else if(inZone && c <= g_zones[i].top && !g_zones[i].dead)
              {
               if(!g_zones[i].inZonePrev)
                 {
                  g_zones[i].touches++;
                  if(g_zones[i].state == 0 && g_zones[i].touches >= 2)
                     g_zones[i].state = 1;
                  if((g_zones[i].state == 1 && g_zones[i].touches > InpMaxTouches) ||
                     (g_zones[i].state == 2 && g_zones[i].touches > 2))
                     g_zones[i].dead = true;
                 }
               bool tradable = !g_zones[i].dead &&
                               ((g_zones[i].state == 1 && g_zones[i].touches >= 2) ||
                                (g_zones[i].state == 2 && g_zones[i].touches >= 1 && g_zones[i].touches <= 2));
               bool okTrend = !InpTrendFilter || trendDown || g_zones[i].state == 2;
               bool okConf  = !InpNeedConfirm || bearConfirm;
               bool fresh   = (g_zones[i].sigTouch != g_zones[i].touches);
               if(tradable && okTrend && okConf && fresh && c < g_zones[i].top)
                 {
                  g_zones[i].sigTouch = g_zones[i].touches;
                  if(g_zones[i].touches == 2)
                    {
                     BufPO2Sell[bar] = h + atr * 0.4;
                     Notify("PO2 SELL — Power of Second Touch at " + ZoneText(g_zones[i]), live);
                    }
                  else
                    {
                     BufSell[bar] = h + atr * 0.3;
                     Notify("SELL — rejection at " + ZoneText(g_zones[i]), live);
                    }
                 }
              }
           }
         g_zones[i].inZonePrev = inZone;
         DrawZone(g_zones[i], time[g_zones[i].bornBar >= InpPivotLen ? g_zones[i].bornBar - InpPivotLen : 0], time[bar]);
        }
     }
   ChartRedraw();
   return rates_total;
  }
//+------------------------------------------------------------------+
