#include "udf.h"
#include <math.h>
#include <string.h>

/* ========================= User-Defined Parameters ========================= */

/*
   WARNING:
   SALT_YI_INDEX must match the Fluent species order.
   This index is zero-based. Check the species list after enabling species transport.
*/
#define SALT_YI_INDEX   0

/*
   Membrane wall Named Selection base names.
   The updated RO spacer geometry splits the former membrane wall into four
   Named Selections:
     - wall_top_mem and wall_bottom_mem are active membrane walls.
     - wall_top_buffer and wall_bottom_buffer are no-slip buffer walls without
       permeation.
   Fluent may further split membrane zones into wall_top_mem.1, wall_top_mem.2,
   wall_bottom_mem.1, wall_bottom_mem.2, etc. after mesh replacement.
   The name-matching logic below accepts the exact active membrane base names
   and these split-zone names, while excluding buffer wall names.
   The solver script calls (update-solver-thread-names) before loading this UDF
   so that THREAD_NAME(t) returns the correct zone name at runtime.
   Spacer walls, periodic boundaries, inlet, outlet, and side walls do NOT match
   these names and will not receive membrane source terms.
*/
#define MEMB_THREAD_BASE_NAME_TOP     "wall_top_mem"
#define MEMB_THREAD_BASE_NAME_BOTTOM  "wall_bottom_mem"

/* Physical constants */
#define MW_SALT         0.05844     /* NaCl molecular weight [kg/mol] */
#define C_INLET_REF     597.8268309 /* Inlet/reference NaCl concentration [mol/m3] */
#define RHO_REF         998.20      /* Reference water density [kg/m3] */

/* Unit conversion */
#define MS_TO_LMH       3600000.0   /* [m/s] to [L/m2/hr] = 3600 s/hr * 1000 L/m3 */

/*
   Source ramp settings.
   These values control how strongly the membrane source terms are applied
   during the early iterations.
*/
#define RAMP_ITER_1       50
#define RAMP_ITER_2      100
#define RAMP_ITER_3      150

#define RAMP_FACTOR_1    0.2
#define RAMP_FACTOR_2    0.5
#define RAMP_FACTOR_3    0.8
#define RAMP_FACTOR_FULL 1.0

/*
   Momentum sink model option.

   0: Isotropic mass-removal momentum sink.
      The total mass sink coefficient is applied to all velocity components.
      This is consistent with treating the local cell mass removal as carrying
      away the local cell momentum.

   1: Normal-projected momentum sink.
      The momentum sink coefficient is projected using squared membrane
      face-normal component ratios. This option is intended for sensitivity
      testing when wall-normal permeation effects need to be isolated.
*/
#define USE_NORMAL_PROJECTED_MOMENTUM_SINK 0

/* Numerical safety threshold */
#define MIN_SOURCE_DENOMINATOR 1.0e-20

/*
   Salt diffusivity [m2/s] for the optional analytic wall-concentration
   reconstruction. MUST match run_config.mass_diffusivity (and the Fluent
   mixture constant-dilute-appx setting). C_DIFF_L reads SV_DIFF_COEFF,
   which is not verified to be allocated for constant-dilute-appx; do not
   use it here. tests/test_udm_layout_parity.py locks this value to
   run_config.mass_diffusivity.
*/
#define D_SALT          2.0e-9

/*
   Analytic wall-concentration reconstruction (default OFF).
   0: physics uses cell-centre c_1 (unchanged).
   1: replace c_m with c_wall = c_p + (c_1 - c_p)*exp(Jw*y1/D_SALT)
      and re-solve the quadratic once (one Picard step).
   y1 is printed on the first ADJUST (geometry, valid immediately).
   CP raw vs reconstructed is printed by on-demand probe_cp_reconstruction
   so it can be triggered after the concentration field has developed.
*/
#ifndef RO_ANALYTIC_CWALL
#define RO_ANALYTIC_CWALL 0
#endif

/*
   Diagnostic storage switches (compile-time).
   Face path is primary (F_UDMI on membrane wall threads). Cell path retains
   the legacy adjacent-cell area-weighted C_UDMI diagnostics for live compare
   until F_UDMI wall-thread allocation is proven on Fluent 25.1.
   Default: both ON. Rebuild with -DRO_UDM_FACE_DIAGNOSTICS=0 or
   -DRO_UDM_CELL_DIAGNOSTICS=0 to isolate one path.
*/
#ifndef RO_UDM_FACE_DIAGNOSTICS
#define RO_UDM_FACE_DIAGNOSTICS 1
#endif
#ifndef RO_UDM_CELL_DIAGNOSTICS
#define RO_UDM_CELL_DIAGNOSTICS 1
#endif

/*
   Membrane parameters are kept as static variables instead of macros so that
   future on-demand or parameter-sweep hooks can modify them at runtime.
*/
static real A_perm = 2.50e-12;      /* Water permeability [m/s/Pa] */
static real B_perm = 2.50e-8;       /* Salt permeability [m/s] */
static real kappa  = 4958.0;        /* Osmotic pressure coefficient [Pa m3/mol] */
static real p_perm = 101325.0;      /* Permeate-side pressure [Pa] */


/* ========================= UDM Index Definition ========================= */

/*
   Required UDM count: UDM_COUNT

   Fluent/PyFluent must allocate at least UDM_COUNT user-defined memory
   locations before this UDF writes or reads C_UDMI / F_UDMI values.

   N_UDM is the Fluent macro for the number of user-defined memory locations
   currently used in Fluent. It is used here to guard UDM access.

   Layout (UDM_SM removed; water sink = TOTAL_S - SI):
     0  UDM_SI          cell   salt mass sink [kg/m3/s]
     1  UDM_TOTAL_S     cell   total mass sink [kg/m3/s]
     2  UDM_XMOM        cell   x-momentum sink coefficient
     3  UDM_YMOM        cell   y-momentum sink coefficient
     4  UDM_ZMOM        cell   z-momentum sink coefficient (slot always
                               reserved; written only under #if RP_3D)
     5  UDM_STRAIN_RATE cell   cell-centred strain rate magnitude [1/s]
     6  UDM_JW          face (+ optional cell) water flux [m/s]
     7  UDM_CM          face (+ optional cell) wall salt conc. [mol/m3]
     8  UDM_LMH         face (+ optional cell) water flux [LMH]
     9  UDM_CP          face (+ optional cell) film-theory CP [-]
    10  UDM_SALT_FLUX   face (+ optional cell) salt mass flux [kg/m2/s]
    11  UDM_AREA        cell   membrane-face area accumulator [m2]
                               (only when RO_UDM_CELL_DIAGNOSTICS)
    12  UDM_Y1          face   wall-to-adjacent-centroid distance [m]

   Face and cell diagnostics share indices 6-10 on different threads so a
   single allocation count covers both. Live compare on wall_top_mem:
     surface-areaavg(udm-8) with FACE=1 CELL=0  vs  FACE=0 CELL=1
   (same index; isolate with the compile switches).

   Default both ON => UDM_COUNT = 13. Index 11 is unused if CELL is off
   so UDM_Y1 stays 12 in every build.
*/
enum {
    UDM_SI = 0,              /* Salt mass source [kg/m3/s] */
    UDM_TOTAL_S = 1,         /* Total mass source [kg/m3/s] */
    UDM_XMOM = 2,            /* X-momentum sink coefficient [kg/m3/s] */
    UDM_YMOM = 3,            /* Y-momentum sink coefficient [kg/m3/s] */
    UDM_ZMOM = 4,            /* Z-momentum sink coefficient [kg/m3/s] */
    UDM_STRAIN_RATE = 5,     /* Cell-centered strain rate magnitude [1/s] */
    UDM_JW = 6,              /* Water flux [m/s] (face primary; cell optional) */
    UDM_CM = 7,              /* Wall salt concentration [mol/m3] */
    UDM_LMH = 8,             /* Water flux [LMH] */
    UDM_CP = 9,              /* Film-theory CP, inlet-referenced [-] */
    UDM_SALT_FLUX = 10,      /* Salt mass flux [kg/m2/s] */
#if RO_UDM_CELL_DIAGNOSTICS
    UDM_AREA = 11,           /* Membrane face area accumulated in adjacent cell */
#endif
    UDM_Y1 = 12,             /* Face: wall-to-centroid normal distance [m] */
    UDM_COUNT = 13
};

static int udm_warning_printed = 0;
static int membrane_thread_warning_printed = 0;
static int cp_denom_warning_printed = 0;
static int wall_y1_diag_done = 0;
static int inlet_G_ready = 0;
static real inlet_G = 1.0;


/* ========================= Utility Functions ========================= */

static int ro_udm_available(void)
{
    if (N_UDM < UDM_COUNT) {
        return 0;
    }

    return 1;
}


static real source_ramp(void)
{
    int iter = N_ITER;

    if (iter < RAMP_ITER_1) return RAMP_FACTOR_1;
    if (iter < RAMP_ITER_2) return RAMP_FACTOR_2;
    if (iter < RAMP_ITER_3) return RAMP_FACTOR_3;

    return RAMP_FACTOR_FULL;
}


static void print_udm_warning(void)
{
#if RP_NODE
    if (I_AM_NODE_ZERO_P) {
        Message("RO UDF warning: insufficient UDM allocation. Required = %d, current = %d.\n",
                UDM_COUNT, N_UDM);
        Message("RO UDF warning: source terms are disabled until enough UDM locations are allocated.\n");
    }
#else
    Message("RO UDF warning: insufficient UDM allocation. Required = %d, current = %d.\n",
            UDM_COUNT, N_UDM);
    Message("RO UDF warning: source terms are disabled until enough UDM locations are allocated.\n");
#endif
}


static int thread_name_matches_base(const char *thread_name, const char *base_name)
{
    size_t base_len;

    if (thread_name == NULL || base_name == NULL) {
        return 0;
    }

    base_len = strlen(base_name);

    if (strcmp(thread_name, base_name) == 0) {
        return 1;
    }

    if (strncmp(thread_name, base_name, base_len) == 0 &&
        thread_name[base_len] == '.') {
        return 1;
    }

    return 0;
}


static int is_membrane_wall_thread(Thread *thread_pointer)
{
    const char *thread_name;

    if (thread_pointer == NULL) {
        return 0;
    }

    thread_name = THREAD_NAME(thread_pointer);

    if (thread_name == NULL) {
        return 0;
    }

    if (thread_name_matches_base(thread_name, MEMB_THREAD_BASE_NAME_TOP)) {
        return 1;
    }

    if (thread_name_matches_base(thread_name, MEMB_THREAD_BASE_NAME_BOTTOM)) {
        return 1;
    }

    return 0;
}


static void print_membrane_thread_warning(void)
{
#if RP_NODE
    if (I_AM_NODE_ZERO_P) {
        Message("RO UDF warning: no membrane wall thread matching '%s' or '%s' was found.\n",
                MEMB_THREAD_BASE_NAME_TOP, MEMB_THREAD_BASE_NAME_BOTTOM);
        Message("RO UDF warning: check membrane wall named selections and make sure (update-solver-thread-names) was executed.\n");
    }
#else
    Message("RO UDF warning: no membrane wall thread matching '%s' or '%s' was found.\n",
            MEMB_THREAD_BASE_NAME_TOP, MEMB_THREAD_BASE_NAME_BOTTOM);
    Message("RO UDF warning: check membrane wall named selections and make sure (update-solver-thread-names) was executed.\n");
#endif
}


/*
   Film-theory CP, inlet-referenced (group 2021/2023 papers):
     cp_perm = B_perm * cm / (Jw + B_perm)                 [mol/m3]
     CP      = (cm - cp_perm) / (C_INLET_REF - cp_perm)    [-]
   Jw must be the UNRAMPED physical flux. Reference concentration is the
   INLET concentration C_INLET_REF (= 597.8268309 mol/m3 ~ 35,000 ppm),
   not the local bulk. Returns 0 and sets *cp_failed=1 if the CP denominator
   is not positive (should be unreachable).
*/
static real ro_compute_cp(real cm, real Jw_phys, int *cp_failed)
{
    real cp_perm;
    real denom;

    *cp_failed = 0;

    if (Jw_phys + B_perm > MIN_SOURCE_DENOMINATOR) {
        cp_perm = B_perm * cm / (Jw_phys + B_perm);
    }
    else {
        cp_perm = 0.0;
    }

    denom = C_INLET_REF - cp_perm;
    if (denom <= 0.0) {
        *cp_failed = 1;
        return 0.0;
    }

    return (cm - cp_perm) / denom;
}


/*
   Invert the film profile
     c(y) - c_p = (c_wall - c_p) * exp(-Jw*y/D)
   at y = y1 with c(y1) = c_1. cp_perm uses the same B*c/(Jw+B) as the
   coupled solution-diffusion model. Jw_phys is the UNRAMPED flux.
*/
static real ro_cwall_from_film(real c_1, real Jw_phys, real y1)
{
    real cp_perm;
    real exponent;
    real c_wall;

    if (Jw_phys + B_perm > MIN_SOURCE_DENOMINATOR) {
        cp_perm = B_perm * c_1 / (Jw_phys + B_perm);
    }
    else {
        cp_perm = 0.0;
    }

    exponent = Jw_phys * y1 / D_SALT;
    c_wall = cp_perm + (c_1 - cp_perm) * exp(exponent);
    if (c_wall < 0.0) {
        c_wall = 0.0;
    }

    return c_wall;
}


/* ========================= Loading and Initialization Hooks ========================= */

DEFINE_EXECUTE_ON_LOADING(RO_UDF_on_loading, libname)
{
    Message("\n");
    Message("RO membrane UDF loaded from library: %s\n", libname);
    Message("Required user-defined memory locations: %d\n", UDM_COUNT);
    Message("Before running the solver, allocate at least %d UDM locations.\n", UDM_COUNT);
    Message("Membrane wall base names: %s, %s\n", MEMB_THREAD_BASE_NAME_TOP, MEMB_THREAD_BASE_NAME_BOTTOM);
    Message("RO_UDM_FACE_DIAGNOSTICS = %d  (F_UDMI on membrane walls)\n",
            RO_UDM_FACE_DIAGNOSTICS);
    Message("RO_UDM_CELL_DIAGNOSTICS = %d  (C_UDMI area-weighted adjacent-cell)\n",
            RO_UDM_CELL_DIAGNOSTICS);
    Message("RO_ANALYTIC_CWALL = %d  (0=cell-centre cm; 1=reconstruct c_wall)\n",
            RO_ANALYTIC_CWALL);
    Message("D_SALT = %.6g m2/s (must match Fluent mixture mass diffusivity)\n",
            D_SALT);
    Message("After convergence, execute on-demand probe_cp_reconstruction\n");
    Message("  TUI: /define/user-defined/execute-on-demand "
            "\"probe_cp_reconstruction::libudf\"\n");
    Message("UDM layout:\n");
    Message("  0  UDM_SI          cell   salt mass sink [kg/m3/s]\n");
    Message("  1  UDM_TOTAL_S     cell   total mass sink [kg/m3/s] (water = TOTAL_S - SI)\n");
    Message("  2  UDM_XMOM        cell   x-momentum sink coefficient\n");
    Message("  3  UDM_YMOM        cell   y-momentum sink coefficient\n");
    Message("  4  UDM_ZMOM        cell   z-momentum sink coefficient\n");
    Message("  5  UDM_STRAIN_RATE cell   cell-centred strain rate [1/s]\n");
    Message("  6  UDM_JW          face/cell water flux [m/s]\n");
    Message("  7  UDM_CM          face/cell wall salt concentration [mol/m3]\n");
    Message("  8  UDM_LMH         face/cell water flux [LMH]\n");
    Message("  9  UDM_CP          face/cell film-theory CP [-]\n");
    Message("     CP = (cm - cp_perm)/(C_INLET_REF - cp_perm),\n");
    Message("     cp_perm = B_perm*cm/(Jw+B_perm), Jw UNRAMPED.\n");
    Message("     C_INLET_REF = %.6g mol/m3 (~35000 ppm inlet), NOT local bulk.\n",
            C_INLET_REF);
    Message(" 10  UDM_SALT_FLUX   face/cell salt mass flux [kg/m2/s]\n");
#if RO_UDM_CELL_DIAGNOSTICS
    Message(" 11  UDM_AREA        cell   membrane area accumulator (cell-diag only)\n");
#endif
    Message(" 12  UDM_Y1          face   wall-to-centroid distance [m]\n");
    Message("Live compare (double-avg cell vs single-avg face) on wall_top_mem:\n");
    Message("  Rebuild FACE=1 CELL=0 vs FACE=0 CELL=1, then surface-areaavg(udm-8).\n");
    Message("  Same index 8; face uses F_UDMI, cell uses area-weighted C_UDMI.\n");
    Message("For PyFluent automation, allocate %d UDM locations.\n", UDM_COUNT);

    if (C_INLET_REF <= 0.0) {
        Message("RO UDF error: C_INLET_REF must be positive. CP calculation will be invalid.\n");
    }

    Message("\n");
}


DEFINE_INIT(RO_UDF_init, d)
{
    udm_warning_printed = 0;
    membrane_thread_warning_printed = 0;
    cp_denom_warning_printed = 0;
    wall_y1_diag_done = 0;
    inlet_G_ready = 0;
    inlet_G = 1.0;
}


/* ========================= Adjust Hook ========================= */

DEFINE_ADJUST(RO_membrane_adjust, d)
{
#if !RP_HOST
    Thread *f_thread;
    Thread *c_thread;
    face_t f;
    cell_t c;
    int membrane_thread_found;
    int cp_failed;

    real Ar[ND_ND];
    real dAm;
    real dVm;

    real rho_ref = RHO_REF;
    real p_op;
    real pabs;
    real dp;
    real Yi_s;
    real cm;
    real S_val;
    real disc;
    real Jw;
    real Js;
    real Jw_phys;
    real Js_phys;
    real salt_mass_flux;
    real salt_mass_flux_phys;
    real cp_face;

    real Sm;
    real Si;
    real Stot;
    real ramp;

    real xf[ND_ND];
    real xc[ND_ND];
    real y1;
    real y1A_local;
    real area_y1_local;
    real y1_min_local;
    real y1_max_local;
    real y1A;
    real area_y1;
    real y1_min;
    real y1_max;

    if (!ro_udm_available()) {
        if (!udm_warning_printed) {
            print_udm_warning();
            udm_warning_printed = 1;
        }
        return;
    }

    ramp = source_ramp();
    p_op = RP_Get_Real("operating-pressure");

    y1A_local = 0.0;
    area_y1_local = 0.0;
    y1_min_local = 1.0e30;
    y1_max_local = -1.0e30;

    /* 1. Initialize cell UDM values (sources + optional cell diagnostics) */
    thread_loop_c(c_thread, d) {
        begin_c_loop(c, c_thread) {
            int i;

            for (i = 0; i < UDM_COUNT; i++) {
                C_UDMI(c, c_thread, i) = 0.0;
            }

            C_UDMI(c, c_thread, UDM_STRAIN_RATE) = C_STRAIN_RATE_MAG(c, c_thread);
        }
        end_c_loop(c, c_thread)
    }

#if RO_UDM_FACE_DIAGNOSTICS
    /* Zero face diagnostic slots on membrane walls before rewrite. */
    thread_loop_f(f_thread, d) {
        if (!is_membrane_wall_thread(f_thread)) {
            continue;
        }
        begin_f_loop(f, f_thread) {
            F_UDMI(f, f_thread, UDM_JW) = 0.0;
            F_UDMI(f, f_thread, UDM_CM) = 0.0;
            F_UDMI(f, f_thread, UDM_LMH) = 0.0;
            F_UDMI(f, f_thread, UDM_CP) = 0.0;
            F_UDMI(f, f_thread, UDM_SALT_FLUX) = 0.0;
            F_UDMI(f, f_thread, UDM_Y1) = 0.0;
        }
        end_f_loop(f, f_thread)
    }
#endif

    /* 2. Loop over all membrane wall threads and accumulate adjacent-cell source terms */
    membrane_thread_found = 0;

    thread_loop_f(f_thread, d) {
        if (!is_membrane_wall_thread(f_thread)) {
            continue;
        }

        membrane_thread_found = 1;

        begin_f_loop(f, f_thread) {
            c = F_C0(f, f_thread);
            c_thread = THREAD_T0(f_thread);

            F_AREA(Ar, f, f_thread);
            dAm = NV_MAG(Ar);
            dVm = C_VOLUME(c, c_thread);

            if (dAm <= 0.0 || dVm <= 0.0) {
                continue;
            }

            /*
               Current implementation uses cell-center pressure and species mass
               fraction from the membrane-adjacent cell. A more detailed model may
               use wall-adjacent reconstructed values.
            */
            pabs = C_P(c, c_thread) + p_op;
            dp   = pabs - p_perm;

            Yi_s = C_YI(c, c_thread, SALT_YI_INDEX);
            Yi_s = MAX(0.0, MIN(Yi_s, 1.0));

            /*
               Constant reference density is used to convert salt mass fraction
               into molar concentration, consistent with the current RO model
               assumption.
            */
            cm = rho_ref * Yi_s / MW_SALT;

            /*
               Normal distance from the wall-face centroid to the adjacent
               cell centroid. F_AREA is the area vector; the projection
               onto the unit normal is | (xc - xf) · Ar | / |Ar|.
            */
            F_CENTROID(xf, f, f_thread);
            C_CENTROID(xc, c, c_thread);
            y1 = fabs(((xc[0] - xf[0]) * Ar[0]
                     + (xc[1] - xf[1]) * Ar[1]
                     + (xc[2] - xf[2]) * Ar[2]) / dAm);

#if RO_UDM_FACE_DIAGNOSTICS
            F_UDMI(f, f_thread, UDM_Y1) = y1;
#endif

            /*
               Coupled solution-diffusion model.
               The quadratic form accounts for permeate concentration through
               cp = Js / Jw.
            */
            S_val = A_perm * (dp - kappa * cm);
            disc  = (S_val + B_perm) * (S_val + B_perm)
                  + 4.0 * A_perm * B_perm * kappa * cm;

            /*
               The discriminant should be non-negative because cm is clamped to
               non-negative values. MAX is retained as a round-off safety guard.
            */
            Jw_phys = 0.5 * (S_val - B_perm + sqrt(MAX(disc, 0.0)));
            Jw_phys = MAX(Jw_phys, 0.0);

            if (Jw_phys + B_perm > MIN_SOURCE_DENOMINATOR) {
                Js_phys = (B_perm * cm * Jw_phys) / (Jw_phys + B_perm);
            }
            else {
                Js_phys = 0.0;
            }

            Js_phys = MAX(Js_phys, 0.0);
            salt_mass_flux_phys = Js_phys * MW_SALT;

            if (!wall_y1_diag_done) {
                y1A_local += y1 * dAm;
                area_y1_local += dAm;
                if (y1 < y1_min_local) y1_min_local = y1;
                if (y1 > y1_max_local) y1_max_local = y1;
            }

#if RO_ANALYTIC_CWALL
            /*
               Reconstruct c_wall from the film profile at y1, then re-solve
               the quadratic once (one Picard step).
            */
            cm = ro_cwall_from_film(cm, Jw_phys, y1);
            S_val = A_perm * (dp - kappa * cm);
            disc  = (S_val + B_perm) * (S_val + B_perm)
                  + 4.0 * A_perm * B_perm * kappa * cm;
            Jw_phys = 0.5 * (S_val - B_perm + sqrt(MAX(disc, 0.0)));
            Jw_phys = MAX(Jw_phys, 0.0);
            if (Jw_phys + B_perm > MIN_SOURCE_DENOMINATOR) {
                Js_phys = (B_perm * cm * Jw_phys) / (Jw_phys + B_perm);
            }
            else {
                Js_phys = 0.0;
            }
            Js_phys = MAX(Js_phys, 0.0);
            salt_mass_flux_phys = Js_phys * MW_SALT;
#endif

            /* Diagnostics use UNRAMPED physical fluxes / film-theory CP. */
            cp_face = ro_compute_cp(cm, Jw_phys, &cp_failed);
            if (cp_failed && !cp_denom_warning_printed) {
#if RP_NODE
                if (I_AM_NODE_ZERO_P) {
                    Message(
                        "RO UDF error: CP denominator (C_INLET_REF - cp_perm) "
                        "is not positive. cm=%.6g Jw_phys=%.6g C_INLET_REF=%.6g\n",
                        cm, Jw_phys, C_INLET_REF
                    );
                }
#else
                Message(
                    "RO UDF error: CP denominator (C_INLET_REF - cp_perm) "
                    "is not positive. cm=%.6g Jw_phys=%.6g C_INLET_REF=%.6g\n",
                    cm, Jw_phys, C_INLET_REF
                );
#endif
                cp_denom_warning_printed = 1;
            }

#if RO_UDM_FACE_DIAGNOSTICS
            F_UDMI(f, f_thread, UDM_JW) = Jw_phys;
            F_UDMI(f, f_thread, UDM_CM) = cm;
            F_UDMI(f, f_thread, UDM_LMH) = Jw_phys * MS_TO_LMH;
            F_UDMI(f, f_thread, UDM_CP) = cp_face;
            F_UDMI(f, f_thread, UDM_SALT_FLUX) = salt_mass_flux_phys;
#endif

            /* Apply source ramp for convergence stability (sources only). */
            Jw = Jw_phys * ramp;
            Js = Js_phys * ramp;

            /*
               Convert salt molar flux to salt mass flux.
               Js is [mol/m2/s], so Js * MW_SALT is [kg/m2/s].
            */
            salt_mass_flux = Js * MW_SALT;

            /* Convert face fluxes to volumetric source coefficients */
            Sm   = Jw * dAm * rho_ref / dVm;       /* Water mass sink [kg/m3/s] */
            Si   = salt_mass_flux * dAm / dVm;     /* Salt mass sink [kg/m3/s] */
            Stot = Sm + Si;                        /* Total mass sink [kg/m3/s] */

            /* Accumulate mass source terms (UDM_SM removed; water = TOTAL_S - SI) */
            C_UDMI(c, c_thread, UDM_SI)      += Si;
            C_UDMI(c, c_thread, UDM_TOTAL_S) += Stot;

#if USE_NORMAL_PROJECTED_MOMENTUM_SINK
            /*
               Optional normal-projected momentum sink.
               Squared component ratios are used to avoid dependency on the sign
               convention of the face-area vector.
            */
            {
                real normal_denom = dAm * dAm;

                C_UDMI(c, c_thread, UDM_XMOM) += Stot * (Ar[0] * Ar[0]) / normal_denom;
                C_UDMI(c, c_thread, UDM_YMOM) += Stot * (Ar[1] * Ar[1]) / normal_denom;
#if RP_3D
                C_UDMI(c, c_thread, UDM_ZMOM) += Stot * (Ar[2] * Ar[2]) / normal_denom;
#endif
            }
#else
            /*
               Default isotropic mass-removal momentum sink. The removed mass is
               assumed to carry away the local cell momentum in each velocity
               component.
            */
            C_UDMI(c, c_thread, UDM_XMOM) += Stot;
            C_UDMI(c, c_thread, UDM_YMOM) += Stot;
#if RP_3D
            C_UDMI(c, c_thread, UDM_ZMOM) += Stot;
#endif
#endif

#if RO_UDM_CELL_DIAGNOSTICS
            /* Area-weighted cell diagnostic accumulation (UNRAMPED). */
            C_UDMI(c, c_thread, UDM_AREA)      += dAm;
            C_UDMI(c, c_thread, UDM_JW)        += Jw_phys * dAm;
            C_UDMI(c, c_thread, UDM_CM)        += cm * dAm;
            C_UDMI(c, c_thread, UDM_LMH)       += (Jw_phys * MS_TO_LMH) * dAm;
            C_UDMI(c, c_thread, UDM_CP)        += cp_face * dAm;
            C_UDMI(c, c_thread, UDM_SALT_FLUX) += salt_mass_flux_phys * dAm;
#endif
        }
        end_f_loop(f, f_thread)
    }

    if (!wall_y1_diag_done) {
#if RP_NODE
        y1A = PRF_GRSUM1(y1A_local);
        area_y1 = PRF_GRSUM1(area_y1_local);
        y1_min = PRF_GRLOW1(y1_min_local);
        y1_max = PRF_GRHIGH1(y1_max_local);
#else
        y1A = y1A_local;
        area_y1 = area_y1_local;
        y1_min = y1_min_local;
        y1_max = y1_max_local;
#endif
        Message0("\n");
        Message0("=== RO_UDF membrane wall y1 ===\n");
        Message0("  RO_ANALYTIC_CWALL            = %d\n", RO_ANALYTIC_CWALL);
        Message0("  D_SALT                       = %.6g m2/s\n", D_SALT);
        if (area_y1 > 0.0) {
            Message0("  y1 area-weighted mean        = %.4g um\n",
                     (y1A / area_y1) * 1.0e6);
            Message0("  y1 min / max                 = %.4g / %.4g um\n",
                     y1_min * 1.0e6, y1_max * 1.0e6);
        }
        else {
            Message0("  WARNING: RO_UDF_Y1_NO_MEMBRANE_AREA "
                     "(no membrane faces contributed to y1)\n");
        }
        Message0("  CP raw vs reconstructed: execute on-demand "
                 "probe_cp_reconstruction after convergence\n");
        Message0("\n");
        wall_y1_diag_done = 1;
    }

    if (!membrane_thread_found && !membrane_thread_warning_printed) {
        print_membrane_thread_warning();
        membrane_thread_warning_printed = 1;
    }

#if RO_UDM_CELL_DIAGNOSTICS
    /* 3. Convert cell diagnostic accumulations to area-weighted averages */
    thread_loop_c(c_thread, d) {
        begin_c_loop(c, c_thread) {
            real Aacc = C_UDMI(c, c_thread, UDM_AREA);

            if (Aacc > 0.0) {
                C_UDMI(c, c_thread, UDM_JW)        /= Aacc;
                C_UDMI(c, c_thread, UDM_CM)        /= Aacc;
                C_UDMI(c, c_thread, UDM_LMH)       /= Aacc;
                C_UDMI(c, c_thread, UDM_CP)        /= Aacc;
                C_UDMI(c, c_thread, UDM_SALT_FLUX) /= Aacc;
            }
        }
        end_c_loop(c, c_thread)
    }
#endif
#endif
}


/* ========================= On-demand CP reconstruction probe =========================
   Read-only walk of membrane faces. Uses cell-centre c_1 and the UNRAMPED
   quadratic Jw (before any Picard re-solve) so the print is the same
   diagnostic whether RO_ANALYTIC_CWALL is 0 or 1. Trigger after
   convergence: /define/user-defined/execute-on-demand
   "probe_cp_reconstruction::libudf"
   ======================================================================= */

DEFINE_ON_DEMAND(probe_cp_reconstruction)
{
#if !RP_HOST
    Domain *d;
    Thread *f_thread;
    Thread *c_thread;
    face_t f;
    cell_t c;
    int cp_failed;

    real Ar[ND_ND];
    real dAm;
    real xf[ND_ND];
    real xc[ND_ND];
    real y1;
    real Yi_s;
    real c_1;
    real cm;
    real p_op;
    real pabs;
    real dp;
    real S_val;
    real disc;
    real Jw_phys;
    real c_wall;
    real cp_raw;
    real cp_recon;

    real area_local;
    real cp_rawA_local;
    real cp_reconA_local;
    real area;
    real cp_rawA;
    real cp_reconA;

    d = Get_Domain(1);
    p_op = RP_Get_Real("operating-pressure");

    area_local = 0.0;
    cp_rawA_local = 0.0;
    cp_reconA_local = 0.0;

    thread_loop_f(f_thread, d) {
        if (!is_membrane_wall_thread(f_thread)) {
            continue;
        }

        begin_f_loop(f, f_thread) {
            c = F_C0(f, f_thread);
            c_thread = THREAD_T0(f_thread);

            F_AREA(Ar, f, f_thread);
            dAm = NV_MAG(Ar);
            if (dAm <= 0.0) {
                continue;
            }

            pabs = C_P(c, c_thread) + p_op;
            dp   = pabs - p_perm;

            Yi_s = C_YI(c, c_thread, SALT_YI_INDEX);
            Yi_s = MAX(0.0, MIN(Yi_s, 1.0));
            cm = RHO_REF * Yi_s / MW_SALT;
            c_1 = cm;

            F_CENTROID(xf, f, f_thread);
            C_CENTROID(xc, c, c_thread);
            y1 = fabs(((xc[0] - xf[0]) * Ar[0]
                     + (xc[1] - xf[1]) * Ar[1]
                     + (xc[2] - xf[2]) * Ar[2]) / dAm);

            S_val = A_perm * (dp - kappa * cm);
            disc  = (S_val + B_perm) * (S_val + B_perm)
                  + 4.0 * A_perm * B_perm * kappa * cm;
            Jw_phys = 0.5 * (S_val - B_perm + sqrt(MAX(disc, 0.0)));
            Jw_phys = MAX(Jw_phys, 0.0);

            c_wall = ro_cwall_from_film(c_1, Jw_phys, y1);
            cp_raw = ro_compute_cp(c_1, Jw_phys, &cp_failed);
            cp_recon = ro_compute_cp(c_wall, Jw_phys, &cp_failed);

            area_local += dAm;
            cp_rawA_local += cp_raw * dAm;
            cp_reconA_local += cp_recon * dAm;
        }
        end_f_loop(f, f_thread)
    }

#if RP_NODE
    area = PRF_GRSUM1(area_local);
    cp_rawA = PRF_GRSUM1(cp_rawA_local);
    cp_reconA = PRF_GRSUM1(cp_reconA_local);
#else
    area = area_local;
    cp_rawA = cp_rawA_local;
    cp_reconA = cp_reconA_local;
#endif

    Message0("\n");
    Message0("=== RO_UDF probe_cp_reconstruction ===\n");
    Message0("  N_ITER                       = %d\n", N_ITER);
    Message0("  RO_ANALYTIC_CWALL            = %d\n", RO_ANALYTIC_CWALL);
    Message0("  D_SALT                       = %.6g m2/s\n", D_SALT);
    if (area > 0.0) {
        Message0("  CP raw (cell-centre)         = %.6g\n",
                 cp_rawA / area);
        Message0("  CP reconstructed             = %.6g\n",
                 cp_reconA / area);
    }
    else {
        Message0("  WARNING: RO_UDF_Y1_NO_MEMBRANE_AREA "
                 "(no membrane faces contributed)\n");
    }
    Message0("\n");
#endif /* !RP_HOST */
}


/* ========================= Source Hooks ========================= */

DEFINE_SOURCE(mass_source, c, t, dS, eqn)
{
    dS[eqn] = 0.0;

    if (!ro_udm_available()) {
        return 0.0;
    }

    return -C_UDMI(c, t, UDM_TOTAL_S);
}


DEFINE_SOURCE(species_salt_source, c, t, dS, eqn)
{
    dS[eqn] = 0.0;

    if (!ro_udm_available()) {
        return 0.0;
    }

    return -C_UDMI(c, t, UDM_SI);
}


DEFINE_SOURCE(x_mom_source, c, t, dS, eqn)
{
    real mtot;

    dS[eqn] = 0.0;

    if (!ro_udm_available()) {
        return 0.0;
    }

    mtot = C_UDMI(c, t, UDM_XMOM);

    dS[eqn] = -mtot;
    return -mtot * C_U(c, t);
}


DEFINE_SOURCE(y_mom_source, c, t, dS, eqn)
{
    real mtot;

    dS[eqn] = 0.0;

    if (!ro_udm_available()) {
        return 0.0;
    }

    mtot = C_UDMI(c, t, UDM_YMOM);

    dS[eqn] = -mtot;
    return -mtot * C_V(c, t);
}


#if RP_3D
DEFINE_SOURCE(z_mom_source, c, t, dS, eqn)
{
    real mtot;

    dS[eqn] = 0.0;

    if (!ro_udm_available()) {
        return 0.0;
    }

    mtot = C_UDMI(c, t, UDM_ZMOM);

    dS[eqn] = -mtot;
    return -mtot * C_W(c, t);
}
#endif


/* ===================== Inlet velocity profile ===========================
   Fully developed plane-Poiseuille profile in z.

   The channel is periodic in y (no walls in y), so the only walls are the
   two membranes at constant z. The fully developed solution is therefore
   1D in z (plane Poiseuille between parallel plates), NOT a 2D
   rectangular-duct profile. No y-dependence must be introduced.

       eta     = (z - INLET_Z_BOTTOM) / CHANNEL_HEIGHT     in [0, 1]
       shape   = 6 * eta * (1 - eta)     exact area-average = 1
       G       = sum(shape_i * dA_i) / sum(dA_i)   = 1 + quadrature excess
       u_i     = U_TARGET * shape_i / G

   eta = 0    -> bottom membrane, u = 0
   eta = 0.5  -> channel centre,  u = 1.5 * U_TARGET / G
   eta = 1    -> top membrane,    u = 0
   discrete area-weighted mean    = U_TARGET  (any mesh)

   G is cached in a file-scope static after the first profile (or probe)
   call. It is computed from ALL face threads matching
   INLET_THREAD_BASE_NAME (including Fluent split names inlet.N).
   Assumption: this case has one inlet. If this hook were attached to a
   second unrelated zone, that zone would still use the inlet-derived G
   from whichever call ran first.

   These additions are 3D-only (read x[2]). The fence below fails a 2D
   compile of this translation unit without changing any existing hook body.
   ======================================================================= */

#if !RP_3D
#error "260813 inlet profile / probe_inlet_profile require a 3D Fluent build."
#endif

/* z of bottom membrane wall [m].
   Server mesh check (channel-centred origin): z extent
   -3.850746e-04 to 3.852144e-04 m, so INLET_Z_BOTTOM = -0.385e-3 and
   CHANNEL_HEIGHT = 0.770e-3 are correct for this mesh generation. */
#define INLET_Z_BOTTOM            (-0.385e-3)
/* Wall-to-wall channel height H [m]. */
#define CHANNEL_HEIGHT            ( 0.770e-3)
/* Requested area-averaged inlet velocity [m/s].
   Patched per case from run_config.inlet_velocity_value onto the
   case-local copy only (see copy_and_patch_udf_to_case_folder).
   Master-file value is a placeholder. */
#define U_TARGET                  0.2
/* Inlet Named Selection base name. Mirrors solver_code_260616.py
   find_zones_by_base_name(..., "inlet"). Fluent may split to inlet.1, ... */
#define INLET_THREAD_BASE_NAME    "inlet"
/* Expected empty-channel inlet area [m2] = W 3.465e-3 m * H 0.770e-3 m.
   D2450_a45-only reference; a mismatch on other geometries is expected. */
#define INLET_AREA_EXPECTED_M2    2.668e-6
/* First-call diagnostic inside inlet profile hook (hook-drop detector). */
#define INLET_PROFILE_DIAGNOSTIC  1
/*
   Sanity band for the discrete shape average G.
   shape(eta) = 6*eta*(1-eta) is strictly concave (shape'' = -12).
   Face-centroid quadrature of a concave function overestimates the
   exact area integral (Jensen), so G >= 1 identically. A measured
   G < 1 is a bug, not a mesh. Lower bound 0.9999 is round-off only.
   Measured excesses: +0.308% to +0.3742% (G = 1.0031 .. 1.0037).
   Upper bound 1.02 is ~5x that excess. Fallback G = 1 (no correction).
*/
#define INLET_G_MIN               0.9999
#define INLET_G_MAX               1.02

/* Shared marker strings for transcript scans (probe + profile). */
#define INLET_PROBE_MARKER_LINE \
    "=== RO_UDF probe_inlet_profile ==="
#define INLET_PROFILE_MARKER_LINE \
    "=== RO_UDF inlet_x_velocity_profile ==="


static int is_inlet_face_thread(Thread *thread_pointer)
{
    const char *thread_name;

    if (thread_pointer == NULL) {
        return 0;
    }

    thread_name = THREAD_NAME(thread_pointer);

    if (thread_name == NULL) {
        return 0;
    }

    return thread_name_matches_base(thread_name, INLET_THREAD_BASE_NAME);
}


static real inlet_poiseuille_shape(real z, real *eta_raw_out, int *clamped_out)
{
    real eta;
    int clamped;

    eta = (z - INLET_Z_BOTTOM) / CHANNEL_HEIGHT;
    if (eta_raw_out != NULL) {
        *eta_raw_out = eta;
    }

    clamped = 0;
    if (eta < 0.0) {
        eta = 0.0;
        clamped = 1;
    }
    if (eta > 1.0) {
        eta = 1.0;
        clamped = 1;
    }

    if (clamped_out != NULL) {
        *clamped_out = clamped;
    }

    return 6.0 * eta * (1.0 - eta);
}


static void ensure_inlet_G(void)
{
    Domain *d;
    Thread *f_thread;
    face_t f;
    real x[ND_ND];
    real area_vec[ND_ND];
    real shape;
    real dA;
    real shapeA_local;
    real area_local;
    real shapeA;
    real area;
    real G;

    if (inlet_G_ready) {
        return;
    }

    shapeA_local = 0.0;
    area_local = 0.0;

    d = Get_Domain(1);
    thread_loop_f(f_thread, d) {
        if (!is_inlet_face_thread(f_thread)) {
            continue;
        }
        begin_f_loop(f, f_thread) {
            F_CENTROID(x, f, f_thread);
            shape = inlet_poiseuille_shape(x[2], NULL, NULL);
            F_AREA(area_vec, f, f_thread);
            dA = NV_MAG(area_vec);
            shapeA_local += shape * dA;
            area_local += dA;
        }
        end_f_loop(f, f_thread)
    }

#if RP_NODE
    shapeA = PRF_GRSUM1(shapeA_local);
    area = PRF_GRSUM1(area_local);
#else
    shapeA = shapeA_local;
    area = area_local;
#endif

    if (area > 0.0) {
        G = shapeA / area;
    }
    else {
        G = 0.0;
    }

    if (!(G > 0.0) || G != G || G < INLET_G_MIN || G > INLET_G_MAX) {
        Message0(
            "WARNING: RO_UDF_INLET_G_OUT_OF_RANGE G=%.6g "
            "(band %.4g .. %.4g); falling back to G=1\n",
            G, INLET_G_MIN, INLET_G_MAX
        );
        G = 1.0;
    }

    inlet_G = G;
    inlet_G_ready = 1;
}


static void print_inlet_profile_stats(
    const char *marker_line,
    real z_min,
    real z_max,
    real eta_min,
    real eta_max,
    int n_clamp,
    int n_face,
    real area_sum,
    real u_area_avg,
    real G)
{
    real excess_pct;

    excess_pct = (G - 1.0) * 100.0;

    /* Message0: cortex/src/cx.h:365 (PARALLEL) / :368 (serial).
       Prefer Message0 over Message+I_AM_NODE_ZERO_P to avoid 50x spam. */
    Message0("\n");
    Message0("%s\n", marker_line);
    Message0("  U_TARGET                     = %.6g m/s\n", U_TARGET);
    Message0("  G                            = %.6g\n", G);
    Message0("  (G-1)*100                    = %.4g %%\n", excess_pct);
    Message0("  INLET_Z_BOTTOM               = %.6g m (%.6g mm)\n",
             INLET_Z_BOTTOM, INLET_Z_BOTTOM * 1000.0);
    Message0("  CHANNEL_HEIGHT               = %.6g m (%.6g mm)\n",
             CHANNEL_HEIGHT, CHANNEL_HEIGHT * 1000.0);
    Message0("  INLET_AREA_EXPECTED_M2       = %.6g m2 (D2450_a45 ref; "
             "mismatch on other geos is expected)\n",
             INLET_AREA_EXPECTED_M2);
    Message0("  inlet face z min/max         = %.6g / %.6g mm\n",
             z_min * 1000.0, z_max * 1000.0);
    Message0("  eta raw min/max (preclamp)   = %.6g / %.6g\n",
             eta_min, eta_max);
    Message0("  faces (global)               = %d\n", n_face);
    Message0("  faces requiring clamp        = %d\n", n_clamp);
    Message0("  inlet area sum               = %.6g m2\n", area_sum);
    Message0("  area-weighted mean u         = %.6g m/s\n", u_area_avg);
    if (n_clamp > 0) {
        Message0(
            "  WARNING: RO_UDF_INLET_GEOMETRY_OUT_OF_RANGE "
            "clamp_count=%d (constants disagree with mesh; continuing)\n",
            n_clamp
        );
    }
    Message0("\n");
}


DEFINE_ON_DEMAND(probe_inlet_profile)
{
#if !RP_HOST
    Domain *d;
    Thread *f_thread;
    face_t f;
    real x[ND_ND];
    real area_vec[ND_ND];
    real z;
    real eta_raw;
    real u_face;
    real dA;
    int clamped;
    int inlet_thread_found;

    real z_min_local;
    real z_max_local;
    real eta_min_local;
    real eta_max_local;
    real area_sum_local;
    real uA_sum_local;
    int n_clamp_local;
    int n_face_local;

    real z_min;
    real z_max;
    real eta_min;
    real eta_max;
    real area_sum;
    real uA_sum;
    int n_clamp;
    int n_face;
    real u_area_avg;

    /* +/-1e30 sentinels so zero-face partitions do not corrupt PRF min/max. */
    z_min_local = 1.0e30;
    z_max_local = -1.0e30;
    eta_min_local = 1.0e30;
    eta_max_local = -1.0e30;
    area_sum_local = 0.0;
    uA_sum_local = 0.0;
    n_clamp_local = 0;
    n_face_local = 0;
    inlet_thread_found = 0;

    d = Get_Domain(1);
    ensure_inlet_G();

    thread_loop_f(f_thread, d) {
        if (!is_inlet_face_thread(f_thread)) {
            continue;
        }

        inlet_thread_found = 1;

        begin_f_loop(f, f_thread) {
            F_CENTROID(x, f, f_thread);
            z = x[2];
            u_face = U_TARGET * inlet_poiseuille_shape(z, &eta_raw, &clamped)
                     / inlet_G;
            /* Read-only probe: do NOT call F_PROFILE. */

            n_face_local += 1;
            if (z < z_min_local) z_min_local = z;
            if (z > z_max_local) z_max_local = z;
            if (eta_raw < eta_min_local) eta_min_local = eta_raw;
            if (eta_raw > eta_max_local) eta_max_local = eta_raw;
            if (clamped) {
                n_clamp_local += 1;
            }

            F_AREA(area_vec, f, f_thread);
            dA = NV_MAG(area_vec);
            area_sum_local += dA;
            uA_sum_local += u_face * dA;
        }
        end_f_loop(f, f_thread)
    }

    /* Every node reaches reductions, including nodes with zero inlet faces. */
#if RP_NODE
    z_min = PRF_GRLOW1(z_min_local);
    z_max = PRF_GRHIGH1(z_max_local);
    eta_min = PRF_GRLOW1(eta_min_local);
    eta_max = PRF_GRHIGH1(eta_max_local);
    area_sum = PRF_GRSUM1(area_sum_local);
    uA_sum = PRF_GRSUM1(uA_sum_local);
    n_clamp = PRF_GISUM1(n_clamp_local);
    n_face = PRF_GISUM1(n_face_local);
    inlet_thread_found = PRF_GIOR1(inlet_thread_found);
#else
    z_min = z_min_local;
    z_max = z_max_local;
    eta_min = eta_min_local;
    eta_max = eta_max_local;
    area_sum = area_sum_local;
    uA_sum = uA_sum_local;
    n_clamp = n_clamp_local;
    n_face = n_face_local;
#endif

    if (area_sum > 0.0) {
        u_area_avg = uA_sum / area_sum;
    }
    else {
        u_area_avg = 0.0;
    }

    print_inlet_profile_stats(
        INLET_PROBE_MARKER_LINE,
        z_min,
        z_max,
        eta_min,
        eta_max,
        n_clamp,
        n_face,
        area_sum,
        u_area_avg,
        inlet_G
    );

    if (!inlet_thread_found) {
        Message0(
            "  WARNING: RO_UDF_INLET_THREAD_NOT_FOUND base='%s'\n",
            INLET_THREAD_BASE_NAME
        );
    }
#endif /* !RP_HOST */
}


DEFINE_PROFILE(inlet_x_velocity_profile, thread, position)
{
#if !RP_HOST
    face_t f;
    real x[ND_ND];
    real eta_raw;
    real u_face;
    int clamped;
#if INLET_PROFILE_DIAGNOSTIC
    /* static first-call flag is PER PROCESS (each compute node has its own). */
    static int inlet_profile_diag_done = 0;
    real z;
    real area_vec[ND_ND];
    real dA;
    real z_min_local;
    real z_max_local;
    real eta_min_local;
    real eta_max_local;
    real area_sum_local;
    real uA_sum_local;
    int n_clamp_local;
    int n_face_local;
    real z_min;
    real z_max;
    real eta_min;
    real eta_max;
    real area_sum;
    real uA_sum;
    int n_clamp;
    int n_face;
    real u_area_avg;
#endif

#if INLET_PROFILE_DIAGNOSTIC
    z_min_local = 1.0e30;
    z_max_local = -1.0e30;
    eta_min_local = 1.0e30;
    eta_max_local = -1.0e30;
    area_sum_local = 0.0;
    uA_sum_local = 0.0;
    n_clamp_local = 0;
    n_face_local = 0;
#endif

    /* Pass 1: lazy G from all inlet threads. Every compute node must
       reach the PRF reduction, including nodes with zero inlet faces. */
    ensure_inlet_G();

    /* Pass 2: assign the normalised profile on this thread. */
    begin_f_loop(f, thread) {
        F_CENTROID(x, f, thread);
        u_face = U_TARGET * inlet_poiseuille_shape(x[2], &eta_raw, &clamped)
                 / inlet_G;
        F_PROFILE(f, thread, position) = u_face;

#if INLET_PROFILE_DIAGNOSTIC
        if (!inlet_profile_diag_done) {
            z = x[2];
            n_face_local += 1;
            if (z < z_min_local) z_min_local = z;
            if (z > z_max_local) z_max_local = z;
            if (eta_raw < eta_min_local) eta_min_local = eta_raw;
            if (eta_raw > eta_max_local) eta_max_local = eta_raw;
            if (clamped) {
                n_clamp_local += 1;
            }
            F_AREA(area_vec, f, thread);
            dA = NV_MAG(area_vec);
            area_sum_local += dA;
            uA_sum_local += u_face * dA;
        }
#endif
    }
    end_f_loop(f, thread)

#if INLET_PROFILE_DIAGNOSTIC
    if (!inlet_profile_diag_done) {
#if RP_NODE
        z_min = PRF_GRLOW1(z_min_local);
        z_max = PRF_GRHIGH1(z_max_local);
        eta_min = PRF_GRLOW1(eta_min_local);
        eta_max = PRF_GRHIGH1(eta_max_local);
        area_sum = PRF_GRSUM1(area_sum_local);
        uA_sum = PRF_GRSUM1(uA_sum_local);
        n_clamp = PRF_GISUM1(n_clamp_local);
        n_face = PRF_GISUM1(n_face_local);
#else
        z_min = z_min_local;
        z_max = z_max_local;
        eta_min = eta_min_local;
        eta_max = eta_max_local;
        area_sum = area_sum_local;
        uA_sum = uA_sum_local;
        n_clamp = n_clamp_local;
        n_face = n_face_local;
#endif

        if (area_sum > 0.0) {
            u_area_avg = uA_sum / area_sum;
        }
        else {
            u_area_avg = 0.0;
        }

        print_inlet_profile_stats(
            INLET_PROFILE_MARKER_LINE,
            z_min,
            z_max,
            eta_min,
            eta_max,
            n_clamp,
            n_face,
            area_sum,
            u_area_avg,
            inlet_G
        );
        inlet_profile_diag_done = 1;
    }
#endif /* INLET_PROFILE_DIAGNOSTIC */
#endif /* !RP_HOST */
}

/* Archived rp-var u_mean pattern: see reference_rp_var_umean_pattern.txt */
