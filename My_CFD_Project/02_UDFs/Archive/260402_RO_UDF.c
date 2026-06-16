#include "udf.h"

/* ========================= User-Defined Parameters ========================= */
#define SALT_YI_INDEX   0    
#define MEMB_THREAD_ID  45   

#define MW_SALT         0.05844     
#define MW_WATER        0.01801528  

static real A_perm = 2.50e-12;   
static real B_perm = 2.50e-8;    
static real kappa  = 4958.0;     
static real p_perm = 101325.0;   
/* =========================================================================== */

DEFINE_ADJUST(RO_membrane_adjust, d)
{
#if !RP_HOST
    Thread *f_thread = Lookup_Thread(d, MEMB_THREAD_ID);
    Thread *c_thread;
    face_t f;
    cell_t c;
    real Ar[ND_ND], dAm, dVm;
    real rho, pabs, dp, Yi_s, cm, S_val, disc, Jw, Js, Sm, Si;

    /* 1. Initialize UDMI for all cells */
    thread_loop_c(c_thread, d) {
        begin_c_loop(c, c_thread) {
            int i;
            for(i=0; i<7; i++) C_UDMI(c, c_thread, i) = 0.0;
        }
        end_c_loop(c, c_thread)
    }

    /* 2. Membrane face loop */
    if (f_thread != NULL) {
        begin_f_loop(f, f_thread) {
            c = F_C0(f, f_thread);
            /* Fixed: THREAD_T0 takes only one argument (the face thread) */
            c_thread = THREAD_T0(f_thread);

            F_AREA(Ar, f, f_thread);
            dAm = NV_MAG(Ar);
            dVm = C_VOLUME(c, c_thread);

            rho = C_R(c, c_thread);
            pabs = C_P(c, c_thread) + RP_Get_Real("operating-pressure");
            dp = pabs - p_perm;

            Yi_s = C_YI(c, c_thread, SALT_YI_INDEX);
            cm = rho * Yi_s / MW_SALT; 

            S_val = A_perm * (dp - kappa * cm);
            disc = (S_val + B_perm) * (S_val + B_perm) + 4.0 * A_perm * B_perm * kappa * cm;
            
            Jw = 0.5 * (S_val - B_perm + sqrt(MAX(disc, 0.0)));
            Jw = MAX(Jw, 0.0); 
            
            Js = (Jw + B_perm > 1e-12) ? (B_perm * cm * Jw) / (Jw + B_perm) : 0.0;

            Si = Js * dAm * MW_SALT / dVm;
            Sm = Jw * dAm * rho / dVm;

            C_UDMI(c, c_thread, 0) += Si;      
            C_UDMI(c, c_thread, 1) += Sm;      
            C_UDMI(c, c_thread, 2) += (Sm + Si); 
            
            C_UDMI(c, c_thread, 3) += Sm * C_U(c, c_thread);
            C_UDMI(c, c_thread, 4) += Sm * C_V(c, c_thread);
#if RP_3D
            C_UDMI(c, c_thread, 5) += Sm * C_W(c, c_thread);
#endif
            C_UDMI(c, c_thread, 5) = Jw; 
            C_UDMI(c, c_thread, 6) = cm; 
        }
        end_f_loop(f, f_thread)
    }
#endif
}

/* Source Hooks (Standard syntax) */
DEFINE_SOURCE(mass_source, c, t, dS, eqn)
{
    real source = -C_UDMI(c, t, 2);
    dS[eqn] = 0.0; 
    return source;
}

DEFINE_SOURCE(species_salt_source, c, t, dS, eqn)
{
    real source = -C_UDMI(c, t, 0);
    dS[eqn] = 0.0;
    return source;
}

DEFINE_SOURCE(x_mom_source, c, t, dS, eqn)
{
    real Sm = C_UDMI(c, t, 1);
    dS[eqn] = -Sm; 
    return -Sm * C_U(c, t);
}

DEFINE_SOURCE(y_mom_source, c, t, dS, eqn)
{
    real Sm = C_UDMI(c, t, 1);
    dS[eqn] = -Sm;
    return -Sm * C_V(c, t);
}

#if RP_3D
DEFINE_SOURCE(z_mom_source, c, t, dS, eqn)
{
    real Sm = C_UDMI(c, t, 1);
    dS[eqn] = -Sm;
    return -Sm * C_W(c, t);
}
#endif