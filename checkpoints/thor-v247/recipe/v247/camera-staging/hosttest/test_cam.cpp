/* THOR CAMERA V247 host test: the pure headers against scripted fakes. Built and run by run.sh. */
#include <assert.h>
#include <map>
#include <stdint.h>
#include <stdio.h>
#include <string>
#include <vector>
#include "PongCamBoard.h"
#include "PongCamCc.h"
#include "PongCamCci.h"
#include "PongCamPower.h"
#include "PongCamInventory.h"

struct Write { uint32_t off, value; };

/* ---- fake CAM_CC: a status bit follows its control bit after `lag` polls unless `stuck` holds it ------------ */
struct Camcc {
  std::map<uint32_t, uint32_t> r;
  std::map<uint32_t, unsigned> wait;
  std::vector<Write> writes;
  unsigned lag = 0, pauses = 0, stuck = 0;
};
static bool is_rcg(uint32_t off) { return off == CamMclkRcg || off == CamCcSlowAhbRcg || off == CamCcCamnocRcg || off == CamCcCci1Rcg; }
static bool is_branch(uint32_t off) { return off == CamMclkCbcr || off == CamCcCpasAhbCbcr || off == CamCcCamnocCbcr || off == CamCcCci1Cbcr; }
static unsigned stuck_bit(uint32_t off) {
  static const struct { uint32_t off; unsigned bit; } table[] = {
      {CamCcTitanGdsc, 1}, {CamMclkRcg, 2}, {CamMclkCbcr, 4}, {CamCcSlowAhbRcg, 8}, {CamCcCamnocRcg, 16},
      {CamCcCci1Rcg, 32}, {CamCcCpasAhbCbcr, 64}, {CamCcCamnocCbcr, 128}, {CamCcCci1Cbcr, 256}};
  for (const auto &t : table) if (t.off == off) return t.bit;
  return 0;
}
static Camcc camcc_reset(unsigned lag) {
  Camcc f;
  f.lag = lag;
  f.r[CamCcTitanGdsc] = CAM_GDSC_SW_COLLAPSE; /* collapsed at boot */
  f.r[CamCcBpsGdsc] = CAM_GDSC_SW_COLLAPSE;
  for (uint32_t off : {CamMclkCbcr, CamCcCpasAhbCbcr, CamCcCamnocCbcr, CamCcCci1Cbcr, CamCcCoreAhbCbcr}) f.r[off] = CAM_CLK_OFF;
  for (uint32_t off : {CamMclkRcg, CamCcSlowAhbRcg, CamCcCamnocRcg, CamCcCci1Rcg}) {
    f.r[off] = 0;                 /* CMD: no update pending */
    f.r[off + 4] = 0x00200445;    /* CFG: source 0, pre-div 0x45, a mode and other bits that must be cleared */
    f.r[off + 8] = 0x11; f.r[off + 12] = 0x22; f.r[off + 16] = 0x33;
  }
  return f;
}
static uint32_t cc_read(void *c, uint32_t off) {
  Camcc &f = *static_cast<Camcc *>(c);
  uint32_t v = f.r.count(off) ? f.r[off] : 0;
  unsigned &w = f.wait[off];
  if (w && --w == 0 && !(f.stuck & stuck_bit(off))) {
    if (off == CamCcTitanGdsc) v = (v & CAM_GDSC_SW_COLLAPSE) ? (v & ~CAM_GDSC_PWR_ON) : (v | CAM_GDSC_PWR_ON);
    else if (is_rcg(off)) v &= ~1u; /* the update latched */
    else if (is_branch(off)) v = (v & 1) ? (v & ~CAM_CLK_OFF) : (v | CAM_CLK_OFF);
    f.r[off] = v;
  }
  return v;
}
static void cc_write(void *c, uint32_t off, uint32_t v) {
  Camcc &f = *static_cast<Camcc *>(c);
  f.writes.push_back(Write{off, v});
  if (off == CamCcTitanGdsc) { f.r[off] = (f.r[off] & CAM_GDSC_PWR_ON) | (v & ~CAM_GDSC_PWR_ON); f.wait[off] = f.lag + 1; }
  else if (is_rcg(off)) { f.r[off] = v; if (v & 1) f.wait[off] = f.lag + 1; }
  else if (is_branch(off)) { f.r[off] = (f.r[off] & CAM_CLK_OFF) | (v & ~CAM_CLK_OFF); f.wait[off] = f.lag + 1; }
  else f.r[off] = v; /* core AHB: halt-delay branch, no status to poll */
}
static void cc_pause(void *c, unsigned us) { Camcc &f = *static_cast<Camcc *>(c); assert(us == 10); ++f.pauses; }
static CamCcOps cc_ops(Camcc &f, unsigned polls = 100) { return CamCcOps{&f, cc_read, cc_write, cc_pause, polls}; }
static const char *passes(const char *msg) { return msg; }

static void test_camcc() {
  for (unsigned lag = 0; lag < 5; ++lag) {
    Camcc f = camcc_reset(lag);
    CamCcOps o = cc_ops(f);
    CamCcRcg saved{};
    cam_cc_rcg_save(&o, CamMclkRcg, &saved);
    assert(saved.cfg == 0x00200445 && saved.m == 0x11 && saved.n == 0x22 && saved.d == 0x33 && saved.cmd == 0);
    unsigned stage = 0;
    assert(cam_cc_mclk_on(&o, CamMclkRcg, CamMclkCbcr, &stage) == CamCcOk && stage == 3);
    assert((f.r[CamCcTitanGdsc] & CAM_GDSC_PWR_ON) && !(f.r[CamCcTitanGdsc] & CAM_GDSC_SW_COLLAPSE));
    assert((f.r[CamMclkRcg + 4] & 0x1037ffu) == 1u && !(f.r[CamMclkRcg] & 1u)); /* BI_TCXO, pre-div 1, update done */
    assert((f.r[CamMclkCbcr] & 1) && !(f.r[CamMclkCbcr] & CAM_CLK_OFF));
    assert(f.writes.size() == 4);
    assert(f.writes[0].off == CamCcTitanGdsc && f.writes[1].off == CamMclkRcg + 4 && f.writes[2].off == CamMclkRcg && f.writes[3].off == CamMclkCbcr);
    assert(f.pauses == 3 * lag);
    assert(cam_cc_mclk_off(&o, CamMclkRcg, CamMclkCbcr, &saved) == CamCcOk);
    assert(!(f.r[CamMclkCbcr] & 1) && (f.r[CamMclkCbcr] & CAM_CLK_OFF));             /* branch off again */
    assert(!(f.r[CamCcTitanGdsc] & CAM_GDSC_PWR_ON) && (f.r[CamCcTitanGdsc] & CAM_GDSC_SW_COLLAPSE)); /* GDSC collapsed */
    assert(f.r[CamMclkRcg + 4] == 0x00200445 && f.r[CamMclkRcg + 8] == 0x11 && f.r[CamMclkRcg + 12] == 0x22 && f.r[CamMclkRcg + 16] == 0x33);
    assert(!(f.r[CamMclkRcg] & 1u)); /* the saved words are latched again: no rate change is left behind */
    assert(f.writes[4].off == CamMclkCbcr && f.writes[5].off == CamMclkRcg + 4 && f.writes[6].off == CamMclkRcg + 8 &&
           f.writes[7].off == CamMclkRcg + 12 && f.writes[8].off == CamMclkRcg + 16 && f.writes[9].off == CamMclkRcg && (f.writes[9].value & 1) &&
           f.writes[10].off == CamCcTitanGdsc);
    /* the other GDSCs are never written: the rear cameras' domains stay as they were */
    for (const Write &w : f.writes) assert(w.off != CamCcBpsGdsc && w.off != CamCcIfe0Gdsc && w.off != CamCcIfe1Gdsc && w.off != CamCcIfe2Gdsc);
  }
  /* each stuck MCLK stage times out, says where, and leaves the GDSC collapsed again */
  for (unsigned s = 0; s < 3; ++s) {
    Camcc f = camcc_reset(1);
    f.stuck = 1u << s;
    CamCcOps o = cc_ops(f, 50);
    unsigned stage = 0;
    assert(cam_cc_mclk_on(&o, CamMclkRcg, CamMclkCbcr, &stage) == CamCcTimeout && stage == s + 1);
    assert(f.r[CamCcTitanGdsc] & CAM_GDSC_SW_COLLAPSE && !(f.r[CamCcTitanGdsc] & CAM_GDSC_PWR_ON));
    assert(f.pauses <= 150);
  }
  /* CCI clocks: GDSC, slow AHB, CAMNOC AXI, CCI1 roots, core AHB (no poll), CPAS AHB, CAMNOC AXI, CCI1 branches */
  {
    Camcc f = camcc_reset(1);
    CamCcOps o = cc_ops(f);
    CamCcRcg saved[3]{};
    cam_cc_rcg_save(&o, CamCcSlowAhbRcg, &saved[0]); cam_cc_rcg_save(&o, CamCcCamnocRcg, &saved[1]); cam_cc_rcg_save(&o, CamCcCci1Rcg, &saved[2]);
    assert(cam_cc_cci_on(&o, CamCcCci1Rcg, CamCcCci1Cbcr) == 0);
    const uint32_t order[] = {CamCcTitanGdsc, CamCcSlowAhbRcg + 4, CamCcSlowAhbRcg, CamCcCamnocRcg + 4, CamCcCamnocRcg,
                              CamCcCci1Rcg + 4, CamCcCci1Rcg, CamCcCoreAhbCbcr, CamCcCpasAhbCbcr, CamCcCamnocCbcr, CamCcCci1Cbcr};
    assert(f.writes.size() == 11);
    for (unsigned i = 0; i < 11; ++i) assert(f.writes[i].off == order[i]);
    assert(f.r[CamCcCoreAhbCbcr] & 1 && !(f.r[CamCcCci1Cbcr] & CAM_CLK_OFF) && !(f.r[CamCcCpasAhbCbcr] & CAM_CLK_OFF));
    assert(cam_cc_cci_off(&o, CamCcCci1Rcg, CamCcCci1Cbcr, saved) == 0);
    assert(f.writes.size() == 30); /* 3 branches, core AHB, then three RCGs x five words */
    const uint32_t back[] = {CamCcCci1Cbcr, CamCcCamnocCbcr, CamCcCpasAhbCbcr, CamCcCoreAhbCbcr,
                             CamCcCci1Rcg + 4, CamCcCci1Rcg + 8, CamCcCci1Rcg + 12, CamCcCci1Rcg + 16, CamCcCci1Rcg,
                             CamCcCamnocRcg + 4, CamCcCamnocRcg + 8, CamCcCamnocRcg + 12, CamCcCamnocRcg + 16, CamCcCamnocRcg,
                             CamCcSlowAhbRcg + 4, CamCcSlowAhbRcg + 8, CamCcSlowAhbRcg + 12, CamCcSlowAhbRcg + 16, CamCcSlowAhbRcg};
    for (unsigned i = 0; i < 19; ++i) assert(f.writes[11 + i].off == back[i]);
    assert(!(f.r[CamCcCoreAhbCbcr] & 1) && (f.r[CamCcCci1Cbcr] & CAM_CLK_OFF) && (f.r[CamCcCpasAhbCbcr] & CAM_CLK_OFF) && (f.r[CamCcCamnocCbcr] & CAM_CLK_OFF));
    assert(f.r[CamCcSlowAhbRcg + 4] == 0x00200445 && f.r[CamCcCci1Rcg + 4] == 0x00200445);
    assert(f.r[CamCcTitanGdsc] & CAM_GDSC_PWR_ON); /* the GDSC is left for the MCLK step */
  }
  /* each stuck CCI step returns its number; the undo clears what was set */
  /* steps: 1 GDSC, 2 slow AHB, 3 CAMNOC AXI, 4 CCI1 root, 5 CPAS AHB, 6 CAMNOC AXI branch, 7 CCI1 branch */
  const unsigned stuck_steps[] = {1, 2, 3, 4, 5, 6, 7};
  const unsigned stuck_bits[] = {1, 8, 16, 32, 64, 128, 256};
  for (unsigned i = 0; i < 7; ++i) {
    Camcc f = camcc_reset(1);
    f.stuck = stuck_bits[i];
    CamCcOps o = cc_ops(f, 30);
    CamCcRcg saved[3]{};
    cam_cc_rcg_save(&o, CamCcSlowAhbRcg, &saved[0]); cam_cc_rcg_save(&o, CamCcCamnocRcg, &saved[1]); cam_cc_rcg_save(&o, CamCcCci1Rcg, &saved[2]);
    assert(cam_cc_cci_on(&o, CamCcCci1Rcg, CamCcCci1Cbcr) == (int)stuck_steps[i]);
    assert(f.pauses <= 8 * 30);
    (void)cam_cc_cci_off(&o, CamCcCci1Rcg, CamCcCci1Cbcr, saved);
    if (stuck_steps[i] != 1) assert(f.r[CamCcCpasAhbCbcr] & CAM_CLK_OFF);
  }
  Camcc f = camcc_reset(0);
  CamCcOps zero{&f, cc_read, cc_write, cc_pause, 0};
  assert(cam_cc_gdsc(&zero, CamCcTitanGdsc, 1) == CamCcArgument && cam_cc_mclk_on(&zero, 0, 0, nullptr) == CamCcArgument);
  assert(cam_cc_cci_on(&zero, 0, 0) == -1 && cam_cc_rcg_restore(&zero, 0, nullptr) == CamCcArgument);
  puts(passes("camcc: MCLK4 GDSC -> RCG XO 19.2 MHz -> branch, CCI roots and branches in order, reverse off with the "
              "boot RCG words latched again, bounded waits, a stuck step reports its number"));
}

/* ---- fake CCI: queues collect loads, QUEUE_START raises a status; a device at dev_addr answers ---------------- */
struct Cci {
  int dev_addr = -1; /* 7-bit address of the fake sensor, -1 = nothing answers */
  uint16_t dev_reg = 0x0016;
  uint8_t id[3] = {0x07, 0x09, 0x00}, len = 2;
  bool any_reg = false, never = false, reset_never = false, nack_read = false;
  uint8_t address[2] = {0, 0};
  uint16_t reg = 0;
  uint32_t status = 0, data = 0;
  unsigned starts = 0, resets = 0, pauses = 0, level = 0;
  int level_override = -1; /* a controller that reports the wrong READ_BUF_LEVEL */
  std::vector<Write> writes;
};
static uint32_t cci_read(void *c, uint32_t off) {
  Cci &f = *static_cast<Cci *>(c);
  if (off == 0xc0c) return f.status;
  if (off == 0x504 || off == 0x604) return 0;
  if (off == 0x21c) return f.level;
  if (off == 0x218) return f.data;
  return 0;
}
static void cci_write(void *c, uint32_t off, uint32_t v) {
  Cci &f = *static_cast<Cci *>(c);
  f.writes.push_back(Write{off, v});
  if (off == 0x004) { ++f.resets; f.status = f.reset_never ? 0 : (1u << 24); return; }
  if (off == 0xc08) { f.status &= ~v; return; }
  if (off == 0x510) {
    if ((v & 0xf) == 1) f.address[0] = uint8_t(v >> 4);
    if ((v & 0xf) == 9) f.reg = uint16_t(((v >> 8) & 0xff) << 8 | ((v >> 16) & 0xff)); /* the 16-bit register address */
  }
  if (off == 0x610 && (v & 0xf) == 1) f.address[1] = uint8_t(v >> 4);
  if (off == 0x008) {
    ++f.starts;
    if (f.never) return;
    const bool here0 = f.dev_addr >= 0 && f.address[0] == f.dev_addr, here1 = f.dev_addr >= 0 && f.address[1] == f.dev_addr;
    if (v == 4) f.status = here0 ? (1u << 16) : (1u << 29); /* QUEUE_START bit 2: master 1, queue 0 */
    if (v == 8) { /* master 1, queue 1 */
      if (!here1 || f.nack_read) { f.status = 1u << 30; return; }
      const bool hit = f.any_reg || f.reg == f.dev_reg; /* another register reads as zeros */
      f.status = 1u << 12;
      f.level = f.level_override >= 0 ? unsigned(f.level_override) : f.len / 4 + 1;
      f.data = hit ? (f.id[0] << 8 | f.id[1] << 16 | f.id[2] << 24) : 0;
    }
  }
}
static void cci_pause(void *c, unsigned us) { Cci &f = *static_cast<Cci *>(c); assert(us == 10); ++f.pauses; }
static CamCciOps cci_ops(Cci &f, unsigned polls = 100) { return CamCciOps{&f, cci_read, cci_write, cci_pause, polls}; }

static void test_cci() {
  uint8_t id[3] = {0, 0, 0};
  /* master 1 of CCI1: queues 0x510/0x610, start bits 4/8, done bits 16/12, data 0x218 */
  {
    Cci f; f.dev_addr = 0x10;
    CamCciOps o = cci_ops(f);
    /* reset: mask, RESET_CMD, the acknowledged status cleared, then master 1's timings at 0x300..0x310 */
    const uint32_t reset_order[] = {0xc04, 0x004, 0xc08, 0xc00, 0x300, 0x304, 0x308, 0x30c, 0x310};
    const uint32_t reset_values[] = {0, 0x0f73f3f7u, 1u << 24, 1, 78u << 16 | 114u, 28u << 16 | 28u, 10u << 16 | 77u, 118, 1u << 8 | 6u << 4 | 1u};
    assert(cam_cci_reset_init_m(&o, 1) == CamCciOk && f.resets == 1 && f.writes.size() == 9);
    for (unsigned i = 0; i < 9; ++i) {
      assert(f.writes[i].off == reset_order[i]);
      if (i != 0) assert(f.writes[i].value == reset_values[i]);
    }
    assert((f.writes[0].value & 0x80000000u) == 0); /* the mask never enables the global bit */
    assert(cam_cci_read16_m(&o, 1, 0x10, 0x0016, id, 2) == CamCciOk && id[0] == 0x07 && id[1] == 0x09);
    /* after the reset's nine writes: the address write (3 loads, count, start bit 2, status cleared), then the
     * read (2 loads, count, start bit 3, status cleared) */
    const Write any = {0, 0xdeadbeefu};
    const Write plan[] = {
        {0x510, 1u | 0x10u << 4}, {0x510, 9u | 2u << 4 | 0x00u << 8 | 0x16u << 16}, {0x510, 8u | 1u << 8}, {0x500, any.value}, /* EXEC_WORD_CNT, the current count of 0x504 written back */
        {0x008, 4}, {0xc08, 1u << 16}, {0xc00, 1},
        {0x610, 1u | 0x10u << 4}, {0x610, 10u | 2u << 4}, {0x600, any.value}, {0x008, 8}, {0xc08, 1u << 12}, {0xc00, 1}};
    assert(f.writes.size() == 9 + 13);
    for (unsigned i = 0; i < 13; ++i) {
      const Write &got = f.writes[9 + i];
      assert(got.off == plan[i].off && (got.value == plan[i].value || plan[i].value == any.value));
    }
    assert(f.starts == 2);
  }
  { Cci f; f.dev_addr = 0x1a; CamCciOps o = cci_ops(f); uint8_t b[3] = {};
    assert(cam_cci_read16_m(&o, 1, 0x10, 0x0016, b, 2) == CamCciNack && f.starts == 1); } /* nothing at 0x10 */
  { Cci f; f.dev_addr = 0x10; f.nack_read = true; CamCciOps o = cci_ops(f); uint8_t b[3] = {};
    assert(cam_cci_read16_m(&o, 1, 0x10, 0x0016, b, 2) == CamCciNack && f.starts == 2); } /* the read is refused */
  { Cci f; f.never = true; CamCciOps o = cci_ops(f, 50); uint8_t b[3] = {};
    assert(cam_cci_read16_m(&o, 1, 0x10, 0x0016, b, 2) == CamCciTimeout && f.pauses == 50); } /* a queue that never ends */
  { Cci f; f.dev_addr = 0x10; f.level_override = 3; CamCciOps o = cci_ops(f); uint8_t b[3] = {};
    assert(cam_cci_read16_m(&o, 1, 0x10, 0x0016, b, 2) == CamCciLength); } /* the level does not match the length */
  Cci none;
  CamCciOps o0 = cci_ops(none);
  assert(cam_cci_read16_m(&o0, 1, 0x80, 0, id, 2) == CamCciArgument && cam_cci_read16_m(&o0, 1, 0x10, 0, id, 12) == CamCciLength);
  assert(cam_cci_read16_m(&o0, 2, 0x10, 0, id, 2) == CamCciArgument && cam_cci_reset_init_m(&o0, 7) == CamCciArgument);
  /* the ordered, bounded list: a Sony answers at the first entry */
  {
    Cci f; f.dev_addr = 0x1a; f.id[0] = 0x05; f.id[1] = 0x96;
    CamCciOps o = cci_ops(f);
    CamIdResult r{};
    assert(cam_cci_identify(&o, 1, kCamIdAttempts, CamIdAttemptCount, nullptr, &r) == CamCciOk);
    assert(r.attempt == kCamIdAttempts + 1 && r.id[0] == 0x05 && r.id[1] == 0x96 && r.id[2] == 0 && f.starts == 3 && f.resets == 2);
  }
  /* an OmniVision at 0x10: the Sony read of 0x0016 comes back as zeros and is not taken for an id */
  {
    Cci f; f.dev_addr = 0x10; f.dev_reg = 0x300a; f.id[0] = 0x32; f.id[1] = 0xa1; f.id[2] = 0x1f; f.len = 3;
    CamCciOps o = cci_ops(f);
    std::vector<unsigned> reported;
    CamIdReport report{&reported, [](void *c, unsigned i, const CamIdAttempt *, int rc, const uint8_t *idv) {
      std::vector<unsigned> *log = static_cast<std::vector<unsigned> *>(c);
      log->push_back(i);
      if (i == 0) assert(rc == CamCciOk && idv[0] == 0 && idv[1] == 0); /* it answered with zeros */
    }};
    CamIdResult r{};
    assert(cam_cci_identify(&o, 1, kCamIdAttempts, CamIdAttemptCount, &report, &r) == CamCciOk);
    assert(r.attempt == kCamIdAttempts + 4 && r.id[0] == 0x32 && r.id[1] == 0xa1 && r.id[2] == 0x1f);
    assert(reported.size() == 5 && reported[4] == 4 && f.resets == 5); /* reset after each miss, none after the hit */
  }
  /* a Sony address that answers 0x0016 with a non-Sony value is not accepted as a Sony id */
  {
    Cci f; f.dev_addr = 0x10; f.any_reg = true; f.id[0] = 0x30; f.id[1] = 0x30;
    CamCciOps o = cci_ops(f);
    CamIdResult r{};
    assert(cam_cci_identify(&o, 1, kCamIdAttempts, CamIdAttemptCount, nullptr, &r) == CamCciOk && r.attempt == kCamIdAttempts + 4);
  }
  /* all ones is no answer either */
  { Cci f; f.dev_addr = 0x10; f.id[0] = 0xff; f.id[1] = 0xff; CamCciOps o = cci_ops(f); CamIdResult r{};
    assert(cam_cci_identify(&o, 1, kCamIdAttempts, CamIdAttemptCount, nullptr, &r) != CamCciOk); }
  /* nothing on the bus: every entry once, a reset between each, then a NACK; it never loops */
  {
    Cci f;
    CamCciOps o = cci_ops(f);
    unsigned reports = 0;
    CamIdReport report{&reports, [](void *c, unsigned, const CamIdAttempt *, int rc, const uint8_t *) { ++*static_cast<unsigned *>(c); assert(rc == CamCciNack); }};
    CamIdResult r{};
    assert(cam_cci_identify(&o, 1, kCamIdAttempts, CamIdAttemptCount, &report, &r) == CamCciNack);
    assert(reports == CamIdAttemptCount && r.attempt == kCamIdAttempts + CamIdAttemptCount && f.starts == CamIdAttemptCount && f.resets == CamIdAttemptCount + 1);
  }
  /* a controller that does not acknowledge its reset: one report, then stop */
  {
    Cci f; f.reset_never = true;
    CamCciOps o = cci_ops(f, 40);
    unsigned reports = 0;
    CamIdReport report{&reports, [](void *c, unsigned, const CamIdAttempt *, int rc, const uint8_t *) { ++*static_cast<unsigned *>(c); assert(rc == CamCciTimeout); }};
    CamIdResult r{};
    assert(cam_cci_identify(&o, 1, kCamIdAttempts, CamIdAttemptCount, &report, &r) == CamCciTimeout && reports == 1 && f.resets == 1);
  }
  { Cci f; CamCciOps o = cci_ops(f); CamIdResult r{};
    assert(cam_cci_identify(&o, 1, kCamIdAttempts, 0, nullptr, &r) == CamCciArgument && cam_cci_identify(&o, 5, kCamIdAttempts, 1, nullptr, &r) == CamCciArgument); }
  /* the table itself: bounded, the first three are Sony, each entry's length fits the read */
  static_assert(CamIdAttemptCount == 8, "eight attempts");
  assert(kCamIdAttempts[0].addr7 == 0x10 && kCamIdAttempts[0].reg == 0x0016 && kCamIdAttempts[0].len == 2);
  for (const CamIdAttempt &a : kCamIdAttempts) assert(a.len >= 1 && a.len <= 11 && a.addr7 <= 0x7f && a.family);
  puts(passes("cci: master 1 of CCI1 reset and timings, chip-id framing (address write, read, echo byte skipped), NACK on "
              "either queue, timeout, length; the ordered id list stops at a plausible answer, resets after a miss, never loops"));
}

/* ---- rails, CMD DB and the power sequence -------------------------------------------------------------- */
static void test_rails() {
  std::vector<Write> votes;
  CamRpmhOps rpmh{&votes, [](void *c, uint32_t address, uint32_t value) { static_cast<std::vector<Write> *>(c)->push_back(Write{address, value}); return 1; }};
  assert(cam_rail_switch(&rpmh, 0x57010, 1104, 1) == 0);
  assert(votes.size() == 3 && votes[0].off == 0x57018 && votes[0].value == CAM_VRM_LDO_HPM);
  assert(votes[1].off == 0x57010 && votes[1].value == 1104 && votes[2].off == 0x57014 && votes[2].value == 1);
  votes.clear();
  assert(cam_rail_switch(&rpmh, 0x57010, 1104, 0) == 0 && votes.size() == 1 && votes[0].off == 0x57014 && votes[0].value == 0);
  struct Failing { std::vector<Write> votes; unsigned n = 0, fail = 0; } fw;
  for (unsigned fail = 0; fail < 3; ++fail) { /* the write that fails stops the three, nothing after it */
    fw.votes.clear(); fw.n = 0; fw.fail = fail + 1;
    CamRpmhOps failing{&fw, [](void *c, uint32_t a, uint32_t v) {
      Failing &x = *static_cast<Failing *>(c);
      x.votes.push_back(Write{a, v});
      return ++x.n == x.fail ? 0 : 1;
    }};
    assert(cam_rail_switch(&failing, 0x57010, 1800, 1) == (int)fail + 1 && fw.votes.size() == fail + 1);
  }
  assert(cam_rail_switch(&rpmh, 0x57011, 1800, 1) == -1 && cam_rail_switch(&rpmh, 0x57010, 99, 1) == -1);
  assert(cam_rail_switch(&rpmh, 0x57010, 0, 1) == -1 && cam_rail_switch(nullptr, 0x57010, 1800, 1) == -1);
  /* the board table: vio, vana, vdig of the live DT; the PPIDs of PM8010J slave 9 */
  assert(CAM_RAIL_COUNT == 3 && kCamRails[0].mv == 1800 && kCamRails[1].mv == 2800 && kCamRails[2].mv == 1104);
  assert(!strcmp(kCamRails[0].name, "ldoj3") && !strcmp(kCamRails[1].name, "ldoj5") && !strcmp(kCamRails[2].name, "ldoj2"));
  assert(kCamRails[0].peripheral == 0x42 && kCamRails[1].peripheral == 0x44 && kCamRails[2].peripheral == 0x41);
  assert(cam_rail_ppid(&kCamRails[0]) == 0x942 && cam_rail_ppid(&kCamRails[1]) == 0x944 && cam_rail_ppid(&kCamRails[2]) == 0x941);
  /* the read-back: ready and enabled at exactly the requested voltage */
  CamRailSample s{0x2a, 0x04, 0x80, 0x80, 1104};
  assert(cam_rail_settled(&s, 1104) && !cam_rail_settled(&s, 1120));
  s.status = 0; assert(!cam_rail_settled(&s, 1104));
  s.status = 0x80; s.enable = 0; assert(!cam_rail_settled(&s, 1104));
  struct Counter { unsigned calls = 0, pauses = 0; } n;
  CamRailWaitOps wait{&n, [](void *c, CamRailSample *out) {
                        Counter &x = *static_cast<Counter *>(c);
                        out->status = out->enable = ++x.calls >= 3 ? 0x80 : 0;
                        out->mv = 1800;
                        return x.calls != 1 ? 1 : 0; /* the first read fails */
                      },
                      [](void *c) { ++static_cast<Counter *>(c)->pauses; }};
  assert(cam_rail_wait(&wait, 1800, 100) == 1 && n.calls == 3 && n.pauses == 2); /* a failing read is just another sample */
  n = Counter{};
  CamRailWaitOps never{&n, [](void *c, CamRailSample *out) {
                         Counter &x = *static_cast<Counter *>(c);
                         ++x.calls;
                         out->status = out->enable = 0x80;
                         out->mv = 1792; /* a neighbour's voltage: not this rail's */
                         return 1;
                       },
                       [](void *c) { ++static_cast<Counter *>(c)->pauses; }};
  assert(cam_rail_wait(&never, 1800, 25) == 0 && n.calls == 25 && n.pauses == 25); /* bounded */
  /* CMD DB in the layout thorCmdDirectory parses */
  std::vector<uint8_t> db(kThorCmdDbBytes, 0);
  auto le16 = [&db](size_t at, unsigned v) { db[at] = uint8_t(v); db[at + 1] = uint8_t(v >> 8); };
  auto le32 = [&le16](size_t at, uint32_t v) { le16(at, v & 0xffff); le16(at + 2, v >> 16); };
  const size_t payload = 144;
  le32(0, 1); le32(4, 0x0c0330db);
  le16(8, 3);            /* table of slave 3 */
  le16(10, 0);           /* entries at payload + 0 */
  le16(12, 24 * 3);      /* aux data at payload + 72 */
  le16(14, 3);           /* three entries */
  const char *names[3] = {"ldoj3", "ldoj5", "ldoj2"};
  const uint32_t addresses[3] = {0x57020, 0x57040, 0x57000};
  for (unsigned i = 0; i < 3; ++i) {
    memcpy(&db[payload + 24 * i], names[i], strlen(names[i]));
    le32(payload + 24 * i + 16, addresses[i]); le16(payload + 24 * i + 20, 8); le16(payload + 24 * i + 22, 0);
  }
  uint32_t address = 0;
  for (unsigned i = 0; i < 3; ++i) assert(cam_cmd_db_find(db.data(), db.size(), names[i], &address) == 0 && address == addresses[i]);
  assert(cam_cmd_db_find(db.data(), db.size(), "ldoj4", &address) == 1);  /* not in this table */
  assert(cam_cmd_db_find(db.data(), db.size(), "ldoj", &address) == 1);   /* a prefix is not a name */
  assert(cam_cmd_db_find(db.data(), db.size(), "mmcx.lvl", &address) == 1);
  assert(cam_cmd_db_find(db.data(), 0x1000, "ldoj3", &address) == 1);     /* a torn copy is refused */
  db[4] = 0; assert(cam_cmd_db_find(db.data(), db.size(), "ldoj3", &address) == 1);
  assert(cam_cmd_db_find(nullptr, db.size(), "ldoj3", &address) == 1 && cam_cmd_db_find(db.data(), db.size(), "ldoj3", nullptr) == 1);
  puts(passes("rails: VRM mode, millivolts, enable in order and enable 0 off, a failed write stops the three; bounded "
              "read-back wait at exactly the requested voltage; CMD DB lookup of ldoj3, ldoj5, ldoj2"));
}

struct Seq {
  std::vector<std::string> log;
  std::string fail_on, fail_off; /* the call whose on (or off) returns an error */
  int id_rc = 0;
  unsigned asserts = 0;
};
static int seq_step(void *c, const char *what, int on) {
  Seq &s = *static_cast<Seq *>(c);
  const std::string label = std::string(what) + (on ? "+" : "-");
  s.log.push_back(label);
  const bool fail = (on && s.fail_on == label) || (!on && s.fail_off == label);
  return fail ? 7 : 0;
}
/* The first "driven low" is the power-up step, every later one the power-down step. */
static int op_reset(void *c, int release) {
  Seq &s = *static_cast<Seq *>(c);
  if (!release) return seq_step(c, "reset0", s.asserts++ == 0);
  return seq_step(c, "reset1", 1);
}
static int op_ahb(void *c, int on) { return seq_step(c, "ahb", on); }
static int op_rail(void *c, unsigned i, int on) { char what[8]; snprintf(what, sizeof what, "rail%u", i); return seq_step(c, what, on); }
static int op_mclk(void *c, int on) { return seq_step(c, "mclk", on); }
static int op_bus(void *c, int on) { return seq_step(c, "bus", on); }
static int op_id(void *c) { Seq &s = *static_cast<Seq *>(c); s.log.push_back("id"); return s.id_rc; }

static void test_sequence() {
  const char *up[] = {"reset0+", "ahb+", "rail0+", "rail1+", "rail2+", "mclk+", "reset1+", "bus+", "id"};
  const char *down[] = {"bus-", "reset0-", "mclk-", "rail2-", "rail1-", "rail0-", "ahb-"};
  auto run = [&](Seq &s, unsigned rails, CamSeqResult *r) {
    CamSeqOps ops{&s, op_reset, op_ahb, op_rail, op_mclk, op_bus, op_id};
    const int rc = cam_sequence(&ops, rails, r);
    return rc;
  };
  auto expect = [](const Seq &s, std::vector<std::string> want) {
    assert(s.log == want);
  };
  { /* everything works: up in order, the id read, then the strict reverse */
    Seq s; CamSeqResult r{};
    assert(run(s, 3, &r) == 0);
    std::vector<std::string> want(up, up + 9); want.insert(want.end(), down, down + 7);
    expect(s, want);
    assert(r.failed_step == 0 && r.id_rc == 0 && r.left == 0);
  }
  { /* the sensor stays silent: the power-up is still undone in full */
    Seq s; s.id_rc = 2; CamSeqResult r{};
    assert(run(s, 3, &r) == 0 && r.failed_step == 0 && r.id_rc == 2 && r.left == 0);
    std::vector<std::string> want(up, up + 9); want.insert(want.end(), down, down + 7);
    expect(s, want);
  }
  { /* the camera AHB clock does not come up: reset low again, the clock bit back, nothing else */
    Seq s; s.fail_on = "ahb+"; CamSeqResult r{};
    assert(run(s, 3, &r) == CamStepAhb && r.failed_step == CamStepAhb && r.failed_rc == 7 && r.id_rc == -1 && r.left == 0);
    expect(s, {"reset0+", "ahb+", "reset0-", "ahb-"});
  }
  { /* the second rail fails: it is switched off again with the first, then the clock; no MCLK, no bus */
    Seq s; s.fail_on = "rail1+"; CamSeqResult r{};
    assert(run(s, 3, &r) == CamStepRail && r.failed_rail == 1 && r.failed_rc == 7);
    expect(s, {"reset0+", "ahb+", "rail0+", "rail1+", "reset0-", "rail1-", "rail0-", "ahb-"});
  }
  { /* MCLK fails: reset, MCLK, the three rails, the clock */
    Seq s; s.fail_on = "mclk+"; CamSeqResult r{};
    assert(run(s, 3, &r) == CamStepMclk);
    expect(s, {"reset0+", "ahb+", "rail0+", "rail1+", "rail2+", "mclk+", "reset0-", "mclk-", "rail2-", "rail1-", "rail0-", "ahb-"});
  }
  { /* the CCI bus fails: it is undone too, then everything below it */
    Seq s; s.fail_on = "bus+"; CamSeqResult r{};
    assert(run(s, 3, &r) == CamStepBus && r.id_rc == -1);
    std::vector<std::string> want(up, up + 8); want.insert(want.end(), down, down + 7);
    expect(s, want);
  }
  { /* the reset release fails: the bus is never started */
    Seq s; s.fail_on = "reset1+"; CamSeqResult r{};
    assert(run(s, 3, &r) == CamStepResetRelease);
    expect(s, {"reset0+", "ahb+", "rail0+", "rail1+", "rail2+", "mclk+", "reset1+", "reset0-", "mclk-", "rail2-", "rail1-", "rail0-", "ahb-"});
  }
  { /* the first reset write fails: the pin is driven low again and the clock bit cleared; no rail */
    Seq s; s.fail_on = "reset0+"; CamSeqResult r{};
    assert(run(s, 3, &r) == CamStepResetAssert && r.left == 0);
    expect(s, {"reset0+", "reset0-", "ahb-"});
  }
  { /* an off call that fails is reported in left, the others still run */
    Seq s; s.fail_off = "rail1-"; CamSeqResult r{};
    assert(run(s, 3, &r) == 0 && r.left == (CAM_SEQ_LEFT_RAIL << 1));
    Seq t; t.fail_off = "bus-"; assert(run(t, 3, &r) == 0 && r.left == CAM_SEQ_LEFT_BUS);
    Seq u; u.fail_off = "reset0-"; assert(run(u, 3, &r) == 0 && r.left == CAM_SEQ_LEFT_RESET); /* the pin stays high */
    Seq v; v.fail_off = "mclk-"; assert(run(v, 3, &r) == 0 && r.left == CAM_SEQ_LEFT_MCLK);
    Seq w; w.fail_off = "ahb-"; assert(run(w, 3, &r) == 0 && r.left == CAM_SEQ_LEFT_AHB);
    Seq x; x.fail_on = "rail2+"; x.fail_off = "rail0-"; assert(run(x, 3, &r) == CamStepRail && r.left == CAM_SEQ_LEFT_RAIL);
  }
  { /* no identify step: the power sequence still runs and is undone */
    Seq s; CamSeqResult r{};
    CamSeqOps ops{&s, op_reset, op_ahb, op_rail, op_mclk, op_bus, nullptr};
    assert(cam_sequence(&ops, 3, &r) == 0 && r.id_rc == -1 && s.log.size() == 15);
  }
  { /* incomplete ops and impossible rail counts are refused before anything runs */
    Seq s; CamSeqResult r{};
    CamSeqOps bad{&s, op_reset, op_ahb, op_rail, op_mclk, op_bus, op_id};
    bad.bus = nullptr; assert(cam_sequence(&bad, 3, &r) == -1);
    bad.bus = op_bus; assert(cam_sequence(&bad, 0, &r) == -1 && cam_sequence(&bad, 5, &r) == -1 && cam_sequence(nullptr, 3, &r) == -1);
    assert(s.log.empty());
  }
  /* the pin words: cam_mclk 1 with 4 mA and no pull on GPIO104, cci_i2c 1 with 4 mA and pull-up on the eGPIO
   * pads (claimed only when bit 11 says so), the reset as a driven output */
  const uint32_t saved104 = 0x000005c6; /* gpio, pull-down, 16 mA, bit 6 set outside our fields */
  assert(cam_pin_config(saved104, CamFuncCamMclk, CamPullNone, 4) == ((saved104 & ~0x3ffu) | (2u << 6) | (1u << 2)));
  assert(cam_pin_config(0x10c00, CamFuncCciI2c, CamPullUp, 4) == (0x10c00u | (2u << 6) | (1u << 2) | 3));
  assert(cam_pin_claim_egpio(0x0088eu) == 0x0188eu && cam_pin_claim_egpio(0x0008eu) == 0x0008eu); /* present 11 -> claim 12; else untouched */
  assert((cam_pin_config(0x000005c6, CamFuncGpio, CamPullDown, 2) | CAM_PIN_OE) == ((0x000005c6u & ~0x3ffu) | (1u << 6) | 1 | (1u << 9)));
  assert(CamPinMclk == 104 && CamPinReset == 108 && CamPinScl == 208 && CamPinSda == 209 && kCamPins[1] == CamPinReset);
  assert(CamMclkRcg == 0x15070 && CamMclkCbcr == 0x15088 && CamCcCci1Rcg == 0x13148 && CamCcCci1Cbcr == 0x13160 && CamCcTitanGdsc == 0x131dc);
  assert(CAM_GCC_CAMERA_AHB_CBCR == 0x136004 && CAM_GCC_CAMERA_XO_CBCR == 0x136020 && CAM_GCC_CAMERA_BCR == 0x136000);
  assert(CamBaseCci1 == 0xac16000 && CamBaseCamcc == 0xade0000 && CamBaseSpmiCore == 0xc400000 && CamBaseSpmiObserver == 0xc440000);
  puts(passes("sequence: reset, AHB clock, vio vana vdig, MCLK4, reset released, CCI bus, id, then the strict reverse; "
              "each injected failure undoes exactly what was attempted; a failed off call is reported in left"));
}

/* ---- level 1: reads only, and the two hardware gates ---------------------------------------------------- */
static void test_inventory() {
  struct Fake {
    std::map<unsigned, unsigned> reads;
    std::map<std::pair<unsigned, uint32_t>, uint32_t> regs; /* (window, offset) -> value; writes to it stand for MMIO */
    bool ahb_on = true, titan_on = true, cpas_on = true, core_on = true;
  } fake;
  auto mmio = [&fake](unsigned window, uint32_t off) -> uint32_t & { return fake.regs[{window, off}]; };
  auto read = [](void *c, unsigned window, uint32_t off) -> uint32_t {
    Fake &f = *static_cast<Fake *>(c);
    assert(window < CamWinCount);
    if (window == CamWinCamcc) assert(f.ahb_on); /* CAM_CC is never read with its clock off */
    if (window >= CamWinCci0 && window <= CamWinIfeLite0) assert(f.ahb_on && f.titan_on && f.cpas_on && f.core_on);
    ++f.reads[window];
    return f.regs[{window, off}]; /* the fake register file: level 1 never writes it */
  };
  CamReadIo io{&fake, read}; /* there is no write entry: level 1 cannot write by construction */
  mmio(CamWinGcc, CAM_GCC_CAMERA_AHB_CBCR) = CAM_CLK_OFF;
  mmio(CamWinGcc, CAM_GCC_CAMERA_BCR) = 1; /* the block is held in reset: reported, never cleared by the probe */
  CamInventory in;
  cam_inventory(&io, &in);
  assert(!in.camcc_read && !in.titan_read && in.gcc_bcr == 1 && fake.reads[CamWinGcc] == 3 && fake.reads[CamWinTlmm] == 8 && fake.reads.size() == 2);
  mmio(CamWinGcc, CAM_GCC_CAMERA_BCR) = 0;
  mmio(CamWinGcc, CAM_GCC_CAMERA_AHB_CBCR) = 0;
  fake.reads.clear();
  cam_inventory(&io, &in);
  assert(in.camcc_read && !in.titan_read); /* titan_top is off: its blocks are not touched */
  assert(fake.reads[CamWinCamcc] == 24 && fake.reads.size() == 3 && !fake.reads.count(CamWinCci1) && !fake.reads.count(CamWinCsid0));
  assert(in.gdscr[8] == 0 && in.gdscr[0] == 0 && in.cpas_cbcr == 0 && in.core_cbcr == 0);
  /* powered with both AHB branches stopped: still not read */
  mmio(CamWinCamcc, CamCcTitanGdsc) = CAM_GDSC_PWR_ON;
  mmio(CamWinCamcc, CamCcCpasAhbCbcr) = mmio(CamWinCamcc, CamCcCoreAhbCbcr) = CAM_CLK_OFF;
  fake.reads.clear();
  cam_inventory(&io, &in);
  assert(!in.titan_read && fake.reads[CamWinCamcc] == 24);
  /* powered and clocked: the version words come from register 0 of each block */
  mmio(CamWinCamcc, CamCcCpasAhbCbcr) = mmio(CamWinCamcc, CamCcCoreAhbCbcr) = 0;
  mmio(CamWinCci0, 0) = 0x20010003; mmio(CamWinCci1, 0) = 0x20010004; mmio(CamWinCsid0, 0) = 0x68000071;
  mmio(CamWinCsidLite0, 0) = 0x68010000; mmio(CamWinIfeLite0, 0) = 0x68000042;
  fake.reads.clear();
  cam_inventory(&io, &in);
  assert(in.titan_read && in.cci0_version == 0x20010003 && in.cci1_version == 0x20010004 && in.csid0_version == 0x68000071);
  assert(in.csid_lite0_version == 0x68010000 && in.ife_lite0_version == 0x68000042);
  for (unsigned w : {unsigned(CamWinCci0), unsigned(CamWinCci1), unsigned(CamWinCsid0), unsigned(CamWinCsidLite0), unsigned(CamWinIfeLite0)})
    assert(fake.reads[w] == 1);
  assert(fake.reads[CamWinCamcc] == 24 && fake.reads[CamWinTlmm] == 8 && fake.reads[CamWinGcc] == 3); /* reads: 3 + 8 + 24 + 5 */
  assert(in.pin_cfg[0] == 0 && kCamPins[0] == 104); /* every read went to the same read-only table */
  /* the snapshot reports what the pins and the clock words hold */
  mmio(CamWinCamcc, CamMclkRcg) = 0x5; mmio(CamWinCamcc, CamMclkRcg + 4) = 0x2345; mmio(CamWinCamcc, CamMclkCbcr) = CAM_CLK_OFF;
  mmio(CamWinTlmm, 104 * 0x1000) = 0x146; mmio(CamWinTlmm, 209 * 0x1000) = 0x1800;
  cam_inventory(&io, &in);
  assert(in.mclk_rcg_cmd == 5 && in.mclk_rcg_cfg == 0x2345 && in.mclk_cbcr == CAM_CLK_OFF && in.pin_cfg[0] == 0x146 && in.pin_cfg[3] == 0x1800);
  cam_inventory(nullptr, &in);
  /* the arbiter table: the channel of a PPID, slave 9 is not slave 8 */
  fake.regs.clear();
  mmio(CamWinSpmi, 4) = 8;
  mmio(CamWinSpmi, 0x2000 + 4 * 0) = 0x941u << 8;
  mmio(CamWinSpmi, 0x2000 + 4 * 3) = 0x842u << 8;
  mmio(CamWinSpmi, 0x2000 + 4 * 5) = 0x942u << 8;
  mmio(CamWinSpmi, 0x2000 + 4 * 7) = 0x944u << 8;
  assert(cam_spmi_channels(&io) == 8);
  assert(cam_spmi_channel(&io, 8, cam_rail_ppid(&kCamRails[2])) == 0 && cam_spmi_channel(&io, 8, cam_rail_ppid(&kCamRails[0])) == 5);
  assert(cam_spmi_channel(&io, 8, cam_rail_ppid(&kCamRails[1])) == 7);
  assert(cam_spmi_channel(&io, 8, 0x842) == 3 && cam_spmi_channel(&io, 8, 0x943) == -1 && cam_spmi_channel(&io, 3, 0x944) == -1);
  /* with nothing mapped the lookup is bounded and says so */
  mmio(CamWinSpmi, 4) = 0;
  assert(cam_spmi_channel(&io, 0, 0x941) == -1);
  puts(passes("inventory: register reads only; CAM_CC is not read with the camera AHB clock off, the CCI/CSID/IFE blocks "
              "not with titan_top off or its AHB branches stopped; the SPMI channel lookup separates slave 9 from slave 8"));
}

int main() {
  test_camcc();
  test_cci();
  test_rails();
  test_sequence();
  test_inventory();
  puts("PASS cam host test (THOR CAMERA V247)");
  return 0;
}
