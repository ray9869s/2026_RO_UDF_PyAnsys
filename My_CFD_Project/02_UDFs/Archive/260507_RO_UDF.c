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
   WARNING:
   MEMB_THREAD_BASE_NAME must match the membrane wall Named Selection base name.
   This name-based version supports disconnected fluid regions where Fluent splits
   one Named Selection into wall, wall.1, wall.2, ... boundary zones.
   The solver script calls (update-solver-thread-names) before compiling/loading
   this UDF so that THREAD_NAME(t) is available.
*/
#define MEMB_THREAD_BASE_NAME  "wall"

/* Physical constants */
#define MW_SALT         0.05844     /* NaCl molecular weight [kg/mol] */
#define C_BULK          603.45      /* Bulk salt concentration [mol/m3] */
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
   Membrane parameters are kept as static variables instead of macros so that
   future DEFINE_ON_DEMAND or parameter-sweep hooks can modify them at runtime.
*/
static real A_perm = 2.50e-12;      /* Water permeability [m/s/Pa] */
static real B_perm = 2.50e-8;       /* Salt permeability [m/s] */
static real kappa  = 4958.0;        /* Osmotic pressure coefficient [Pa m3/mol] */
static real p_perm = 101325.0;      /* Permeate-side pressure [Pa] */


/* ========================= UDM Index Definition ========================= */

/*
   Required UDM count: UDM_COUNT

   Fluent/PyFluent must allocate at least UDM_COUNT user-defined memory
   locations before this UDF writes or reads C_UDMI values.

   N_UDM is the Fluent macro for the number of user-defined memory locations
   currently used in Fluent. It is used here to guard C_UDMI access.
*/
enum {
    UDM_SI = 0,          /* Salt mass source [kg/m3/s] */
    UDM_SM = 1,          /* Water mass source [kg/m3/s] */
    UDM_TOTAL_S = 2,     /* Total mass source [kg/m3/s] */
    UDM_XMOM = 3,        /* X-momentum sink coefficient [kg/m3/s] */
    UDM_YMOM = 4,        /* Y-momentum sink coefficient [kg/m3/s] */
    UDM_ZMOM = 5,        /* Z-momentum sink coefficient [kg/m3/s] */
    UDM_JW = 6,          /* Area-averaged water flux [m/s] */
    UDM_CM = 7,          /* Area-averaged salt concentration [mol/m3] */
    UDM_LMH = 8,         /* Area-averaged water flux [LMH] */
    UDM_CP = 9,          /* Area-averaged concentration polarization modulus [-] */
    UDM_SHEAR = 10,      /* Shear rate [1/s] */
    UDM_AREA = 11,       /* Membrane face area accumulated in the cell [m2] */
    UDM_SALT_FLUX = 12,  /* Area-averaged salt mass flux [kg/m2/s] */
    UDM_COUNT = 13
};

static int udm_warning_printed = 0;
static int membrane_thread_warning_printed = 0;


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


static int is_membrane_wall_thread(Thread *thread_pointer)
{
    const char *thread_name;
    size_t base_len;

    if (thread_pointer == NULL) {
        return 0;
    }

    thread_name = THREAD_NAME(thread_pointer);

    if (thread_name == NULL) {
        return 0;
    }

    base_len = strlen(MEMB_THREAD_BASE_NAME);

    if (strcmp(thread_name, MEMB_THREAD_BASE_NAME) == 0) {
        return 1;
    }

    if (strncmp(thread_name, MEMB_THREAD_BASE_NAME, base_len) == 0 &&
        thread_name[base_len] == '.') {
        return 1;
    }

    return 0;
}


static void print_membrane_thread_warning(void)
{
#if RP_NODE
    if (I_AM_NODE_ZERO_P) {
        Message("RO UDF warning: no membrane wall thread matching base name '%s' was found.\n",
                MEMB_THREAD_BASE_NAME);
        Message("RO UDF warning: check MEMB_THREAD_BASE_NAME and make sure (update-solver-thread-names) was executed.\n");
    }
#else
    Message("RO UDF warning: no membrane wall thread matching base name '%s' was found.\n",
            MEMB_THREAD_BASE_NAME);
    Message("RO UDF warning: check MEMB_THREAD_BASE_NAME and make sure (update-solver-thread-names) was executed.\n");
#endif
}


/* ========================= Loading and Initialization Hooks ========================= */

DEFINE_EXECUTE_ON_LOADING(RO_UDF_on_loading, libname)
{
    Message("\n");
    Message("RO membrane UDF loaded from library: %s\n", libname);
    Message("Required user-defined memory locations: %d\n", UDM_COUNT);
    Message("Before running the solver, allocate at least %d UDM locations.\n", UDM_COUNT);
    Message("Membrane wall base name: %s\n", MEMB_THREAD_BASE_NAME);
    Message("Additional diagnostic: UDM_12 = area-averaged salt mass flux [kg/m2/s]\n");
    Message("For PyFluent automation, allocate %d UDM locations.\n", UDM_COUNT);

    if (C_BULK <= 0.0) {
        Message("RO UDF error: C_BULK must be positive. CP modulus calculation will be invalid.\n");
    }

    Message("\n");
}


DEFINE_INIT(RO_UDF_init, d)
{
    udm_warning_printed = 0;
    membrane_thread_warning_printed = 0;
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
    real salt_mass_flux;

    real Sm;
    real Si;
    real Stot;
    real ramp;

    if (!ro_udm_available()) {
        if (!udm_warning_printed) {
            print_udm_warning();
            udm_warning_printed = 1;
        }
        return;
    }

    ramp = source_ramp();
    p_op = RP_Get_Real("operating-pressure");

    /* 1. Initialize UDM values */
    thread_loop_c(c_thread, d) {
        begin_c_loop(c, c_thread) {
            int i;

            for (i = 0; i < UDM_COUNT; i++) {
                C_UDMI(c, c_thread, i) = 0.0;
            }

            C_UDMI(c, c_thread, UDM_SHEAR) = C_STRAIN_RATE_MAG(c, c_thread);
        }
        end_c_loop(c, c_thread)
    }

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
            Jw = 0.5 * (S_val - B_perm + sqrt(MAX(disc, 0.0)));
            Jw = MAX(Jw, 0.0);

            if (Jw + B_perm > MIN_SOURCE_DENOMINATOR) {
                Js = (B_perm * cm * Jw) / (Jw + B_perm);
            }
            else {
                Js = 0.0;
            }

            Js = MAX(Js, 0.0);

            /* Apply source ramp for convergence stability */
            Jw *= ramp;
            Js *= ramp;

            /*
               Convert salt molar flux to salt mass flux.
               Js is [mol/m2/s], so Js * MW_SALT is [kg/m2/s].
            */
            salt_mass_flux = Js * MW_SALT;

            /* Convert face fluxes to volumetric source coefficients */
            Sm   = Jw * dAm * rho_ref / dVm;       /* Water mass sink [kg/m3/s] */
            Si   = salt_mass_flux * dAm / dVm;     /* Salt mass sink [kg/m3/s] */
            Stot = Sm + Si;                        /* Total mass sink [kg/m3/s] */

            /* Accumulate mass source terms */
            C_UDMI(c, c_thread, UDM_SI)      += Si;
            C_UDMI(c, c_thread, UDM_SM)      += Sm;
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

            /* Area-weighted diagnostic accumulation */
            C_UDMI(c, c_thread, UDM_AREA)      += dAm;
            C_UDMI(c, c_thread, UDM_JW)        += Jw * dAm;
            C_UDMI(c, c_thread, UDM_CM)        += cm * dAm;
            C_UDMI(c, c_thread, UDM_LMH)       += (Jw * MS_TO_LMH) * dAm;
            C_UDMI(c, c_thread, UDM_CP)        += (cm / C_BULK) * dAm;
            C_UDMI(c, c_thread, UDM_SALT_FLUX) += salt_mass_flux * dAm;
        }
        end_f_loop(f, f_thread)
    }

    if (!membrane_thread_found && !membrane_thread_warning_printed) {
        print_membrane_thread_warning();
        membrane_thread_warning_printed = 1;
    }

    /* 3. Convert diagnostic accumulations to area-weighted averages */
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
