#include "udf.h"

/* ========================= User-Defined Parameters ========================= */
#define SALT_YI_INDEX   0
#define MEMB_THREAD_ID  45

#define MW_SALT         0.05844
#define MW_WATER        0.01801528
#define C_BULK          603.45      /* (mol/m3) */
#define RHO_REF         998.20      /* (kg/m3) */

static real A_perm = 2.50e-12;
static real B_perm = 2.50e-8;
static real kappa  = 4958.0;
static real p_perm = 101325.0;

/* UDM 인덱스 정의 */
enum {
    UDM_SI = 0,       /* Salt mass source [kg/m3/s] */
    UDM_SM = 1,       /* Water mass source [kg/m3/s] */
    UDM_TOTAL_S = 2,  /* Total mass source [kg/m3/s] */
    UDM_XMOM = 3,     /* X-momentum sink coeff */
    UDM_YMOM = 4,     /* Y-momentum sink coeff */
    UDM_ZMOM = 5,     /* Z-momentum sink coeff */
    UDM_JW = 6,       /* Area-averaged water flux [m/s] */
    UDM_CM = 7,       /* Area-averaged salt concentration [mol/m3] */
    UDM_LMH = 8,      /* Area-averaged water flux [LMH] */
    UDM_CP = 9,       /* Area-averaged CP modulus [-] */
    UDM_SHEAR = 10,   /* Shear rate [1/s] */
    UDM_AREA = 11     /* Membrane face area accumulated in the cell [m2] */
};

/* optional: source ramp for easier convergence */
static real source_ramp(void)
{
    int iter = N_ITER;   /* Fluent iteration count */
    if (iter < 50) return 0.2;
    if (iter < 100) return 0.5;
    if (iter < 150) return 0.8;
    return 1.0;
}

DEFINE_ADJUST(RO_membrane_adjust, d)
{
#if !RP_HOST
    Thread *f_thread = Lookup_Thread(d, MEMB_THREAD_ID);
    Thread *c_thread;
    face_t f;
    cell_t c;
    real Ar[ND_ND], dAm, dVm;
    real rho_ref = RHO_REF;
    real pabs, dp, Yi_s, cm, S_val, disc, Jw, Js;
    real Sm, Si, Stot, ramp;

    ramp = source_ramp();

    /* 1. Initialize UDMI */
    thread_loop_c(c_thread, d) {
        begin_c_loop(c, c_thread) {
            int i;
            for (i = 0; i < 12; i++) C_UDMI(c, c_thread, i) = 0.0;
            C_UDMI(c, c_thread, UDM_SHEAR) = C_STRAIN_RATE_MAG(c, c_thread);
        }
        end_c_loop(c, c_thread)
    }

    /* 2. Membrane face loop */
    if (f_thread != NULL) {
        begin_f_loop(f, f_thread) {
            c = F_C0(f, f_thread);
            c_thread = THREAD_T0(f_thread);

            F_AREA(Ar, f, f_thread);
            dAm = NV_MAG(Ar);
            dVm = C_VOLUME(c, c_thread);

            /* 현재는 cell-center 값 사용.
               더 엄밀하게 하려면 wall-adjacent reconstructed value 사용 권장 */
            pabs = C_P(c, c_thread) + RP_Get_Real("operating-pressure");
            dp   = pabs - p_perm;

            Yi_s = C_YI(c, c_thread, SALT_YI_INDEX);
            Yi_s = MAX(0.0, MIN(Yi_s, 1.0));

            /* 논문 가정에 맞춰 constant rho_ref 사용 */
            cm = rho_ref * Yi_s / MW_SALT;

            /* solution-diffusion model */
            S_val = A_perm * (dp - kappa * cm);
            disc  = (S_val + B_perm) * (S_val + B_perm)
                  + 4.0 * A_perm * B_perm * kappa * cm;

            Jw = 0.5 * (S_val - B_perm + sqrt(MAX(disc, 0.0)));
            Jw = MAX(Jw, 0.0);

            Js = (Jw + B_perm > 1e-20) ? (B_perm * cm * Jw) / (Jw + B_perm) : 0.0;
            Js = MAX(Js, 0.0);

            /* ramp for convergence */
            Jw *= ramp;
            Js *= ramp;

            /* volumetric sinks */
            Sm   = Jw * dAm * rho_ref   / dVm;      /* water mass sink */
            Si   = Js * dAm * MW_SALT  / dVm;       /* salt mass sink */
            Stot = Sm + Si;                         /* total mass sink */

            /* source accumulation */
            C_UDMI(c, c_thread, UDM_SI)      += Si;
            C_UDMI(c, c_thread, UDM_SM)      += Sm;
            C_UDMI(c, c_thread, UDM_TOTAL_S) += Stot;

            /* momentum sink는 total mass sink 기준으로 맞춤 */
            C_UDMI(c, c_thread, UDM_XMOM) += Stot;
            C_UDMI(c, c_thread, UDM_YMOM) += Stot;
#if RP_3D
            C_UDMI(c, c_thread, UDM_ZMOM) += Stot;
#endif

            /* diagnostics: area-weighted accumulation */
            C_UDMI(c, c_thread, UDM_AREA) += dAm;
            C_UDMI(c, c_thread, UDM_JW)   += Jw * dAm;
            C_UDMI(c, c_thread, UDM_CM)   += cm * dAm;
            C_UDMI(c, c_thread, UDM_LMH)  += (Jw * 3600000.0) * dAm;
            C_UDMI(c, c_thread, UDM_CP)   += (cm / C_BULK) * dAm;
        }
        end_f_loop(f, f_thread)
    }

    /* 3. Convert diagnostics to area-weighted averages */
    thread_loop_c(c_thread, d) {
        begin_c_loop(c, c_thread) {
            real Aacc = C_UDMI(c, c_thread, UDM_AREA);
            if (Aacc > 0.0) {
                C_UDMI(c, c_thread, UDM_JW)  /= Aacc;
                C_UDMI(c, c_thread, UDM_CM)  /= Aacc;
                C_UDMI(c, c_thread, UDM_LMH) /= Aacc;
                C_UDMI(c, c_thread, UDM_CP)  /= Aacc;
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
    return -C_UDMI(c, t, UDM_TOTAL_S);
}

DEFINE_SOURCE(species_salt_source, c, t, dS, eqn)
{
    dS[eqn] = 0.0;
    return -C_UDMI(c, t, UDM_SI);
}

DEFINE_SOURCE(x_mom_source, c, t, dS, eqn)
{
    real mtot = C_UDMI(c, t, UDM_XMOM);
    dS[eqn] = -mtot;
    return -mtot * C_U(c, t);
}

DEFINE_SOURCE(y_mom_source, c, t, dS, eqn)
{
    real mtot = C_UDMI(c, t, UDM_YMOM);
    dS[eqn] = -mtot;
    return -mtot * C_V(c, t);
}

#if RP_3D
DEFINE_SOURCE(z_mom_source, c, t, dS, eqn)
{
    real mtot = C_UDMI(c, t, UDM_ZMOM);
    dS[eqn] = -mtot;
    return -mtot * C_W(c, t);
}
#endif