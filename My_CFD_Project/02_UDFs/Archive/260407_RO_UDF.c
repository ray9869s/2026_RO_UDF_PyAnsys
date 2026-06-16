#include "udf.h"

/* ========================= User-Defined Parameters ========================= */
#define SALT_YI_INDEX   0    
#define MEMB_THREAD_ID  45   

#define MW_SALT         0.05844     
#define MW_WATER        0.01801528  
#define C_BULK          597.83       /* 3.5 wt% NaCl 용액의 몰 농도 (mol/m3) */

static real A_perm = 2.50e-12;   
static real B_perm = 2.50e-8;    
static real kappa  = 4958.0;     
static real p_perm = 101325.0;   

/* UDM 인덱스 정의 (가독성을 위해) */
enum {
    UDM_SI = 0,      /* Salt Mass Source */
    UDM_SM = 1,      /* Water Mass Source */
    UDM_TOTAL_S = 2, /* Total Mass Source */
    UDM_XMOM = 3,    /* X-Momentum Source */
    UDM_YMOM = 4,    /* Y-Momentum Source */
    UDM_ZMOM = 5,    /* Z-Momentum Source */
    UDM_JW = 6,      /* Water Flux (m/s) */
    UDM_CM = 7,      /* Salt Concentration (mol/m3) */
    UDM_LMH = 8,     /* Water Flux (LMH) */
    UDM_CP = 9,      /* CP Modulus (-) */
    UDM_SHEAR = 10   /* Shear Rate (1/s) */
};

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

    /* 1. Initialize UDMI (0~10번까지 초기화) */
    thread_loop_c(c_thread, d) {
        begin_c_loop(c, c_thread) {
            int i;
            for(i=0; i<11; i++) C_UDMI(c, c_thread, i) = 0.0;
            
            /* Shear Rate는 모든 영역에서 보고 싶을 수 있으므로 여기서 계산 */
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

            /* 원천항 저장 */
            C_UDMI(c, c_thread, UDM_SI) += Si;      
            C_UDMI(c, c_thread, UDM_SM) += Sm;      
            C_UDMI(c, c_thread, UDM_TOTAL_S) += (Sm + Si); 
            C_UDMI(c, c_thread, UDM_XMOM) += Sm * C_U(c, c_thread);
            C_UDMI(c, c_thread, UDM_YMOM) += Sm * C_V(c, c_thread);
#if RP_3D
            C_UDMI(c, c_thread, UDM_ZMOM) += Sm * C_W(c, c_thread);
#endif

            /* 분석용 변수 저장 */
            C_UDMI(c, c_thread, UDM_JW) = Jw;             /* (m/s) */
            C_UDMI(c, c_thread, UDM_CM) = cm;             /* (mol/m3) */
            C_UDMI(c, c_thread, UDM_LMH) = Jw * 3600000.0; /* (L/m2.h) 환산: m/s * 1000 * 3600 */
            C_UDMI(c, c_thread, UDM_CP) = cm / C_BULK;    /* CP Modulus */
        }
        end_f_loop(f, f_thread)
    }
#endif
}

/* Source Hooks (기존 코드와 동일하게 UDM 인덱스 적용) */
DEFINE_SOURCE(mass_source, c, t, dS, eqn) { return -C_UDMI(c, t, UDM_TOTAL_S); }
DEFINE_SOURCE(species_salt_source, c, t, dS, eqn) { return -C_UDMI(c, t, UDM_SI); }
DEFINE_SOURCE(x_mom_source, c, t, dS, eqn) { dS[eqn] = -C_UDMI(c, t, UDM_SM); return -C_UDMI(c, t, UDM_SM) * C_U(c, t); }
DEFINE_SOURCE(y_mom_source, c, t, dS, eqn) { dS[eqn] = -C_UDMI(c, t, UDM_SM); return -C_UDMI(c, t, UDM_SM) * C_V(c, t); }
#if RP_3D
DEFINE_SOURCE(z_mom_source, c, t, dS, eqn) { dS[eqn] = -C_UDMI(c, t, UDM_SM); return -C_UDMI(c, t, UDM_SM) * C_W(c, t); }
#endif