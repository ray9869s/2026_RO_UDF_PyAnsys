#include "udf.h"

/* ========================= 사용자 설정 파라미터 ========================= */
#define SALT_YI_INDEX   0    /* Mixture material에서 Salt의 Index 확인 필요 */
#define MEMB_THREAD_ID  28   /* Fluent Meshing에서 확인한 Membrane Wall ID */

#define MW_SALT         0.05844   /* NaCl 기준 [kg/mol] */
#define MW_WATER        0.01801528 /* [kg/mol] */

static real A_perm = 2.50e-12;   /* 수투과 계수 [m/(s·Pa)] */
static real B_perm = 2.50e-8;    /* 염투과 계수 [m/s] */
static real kappa  = 4958.0;     /* 삼투압 계수 [m^3·Pa/mol] */
static real p_perm = 101325.0;   /* Permeate 쪽 절대 압력 [Pa] */

/* ======================================================================= */

DEFINE_ADJUST(RO_membrane_adjust, d)
{
#if !RP_HOST
    Thread *f_thread = Lookup_Thread(d, MEMB_THREAD_ID);
    Thread *c_thread;
    face_t f;
    cell_t c;
    real Ar[ND_ND], dAm, dVm;
    real rho, pabs, dp, Yi_s, cm, S_val, disc, Jw, Js, Sm, Si;

    /* 1. 모든 셀의 UDMI 초기화 (매 Adjust 단계마다 초기화 후 누적) */
    thread_loop_c(c_thread, d) {
        begin_c_loop(c, c_thread) {
            int i;
            for(i=0; i<22; i++) C_UDMI(c, c_thread, i) = 0.0;
        }
        end_c_loop(c, c_thread)
    }

    /* 2. 멤브레인 면(Face) 루프: 인접 셀에 소스 항 계산 및 저장 */
    if (f_thread != NULL) {
        begin_f_loop(f, f_thread) {
            c = F_C0(f, f_thread);
            c_thread = THREAD_T0(f, f_thread);

            F_AREA(Ar, f, f_thread);
            dAm = NV_MAG(Ar);
            dVm = C_VOLUME(c, c_thread);

            rho = C_R(c, c_thread);
            pabs = C_P(c, c_thread) + RP_Get_Real("operating-pressure");
            dp = pabs - p_perm;

            Yi_s = C_YI(c, c_thread, SALT_YI_INDEX);
            cm = rho * Yi_s / MW_SALT; /* 몰 농도 변환 [mol/m^3] */

            /* Gu et al. (2017)의 Solution-Diffusion 연립 방정식 풀이 (근의 공식) */
            S_val = A_perm * (dp - kappa * cm);
            disc = (S_val + B_perm) * (S_val + B_perm) + 4.0 * A_perm * B_perm * kappa * cm;
            
            /* 수치적 안정성을 위한 MAX 처리 */
            Jw = 0.5 * (S_val - B_perm + sqrt(MAX(disc, 0.0)));
            Jw = MAX(Jw, 0.0);
            
            Js = (Jw + B_perm > 1e-12) ? (B_perm * cm * Jw) / (Jw + B_perm) : 0.0;

            /* 체적 소스항 계산 [kg/(m^3·s)] */
            Sm = Jw * dAm * rho / dVm;      /* Water mass removal rate */
            Si = Js * dAm * MW_SALT / dVm;   /* Salt mass removal rate */

            /* UDMI에 누적 (+=를 사용하여 한 셀이 여러 면을 공유할 때 대응) */
            C_UDMI(c, c_thread, 0) += Si;      /* Salt Source (Si) */
            C_UDMI(c, c_thread, 1) += Sm;      /* Water Source (Sm) */
            C_UDMI(c, c_thread, 2) += (Sm + Si); /* Total Mass Source */
            
            /* Momentum Source 기록용 */
            C_UDMI(c, c_thread, 3) += Sm * C_U(c, c_thread);
            C_UDMI(c, c_thread, 4) += Sm * C_V(c, c_thread);
#if RP_3D
            C_UDMI(c, c_thread, 21) += Sm * C_W(c, c_thread);
#endif
            /* 모니터링용 (마지막 면 기준 또는 평균적 의미) */
            C_UDMI(c, c_thread, 5) = Jw;
            C_UDMI(c, c_thread, 6) = cm;
        }
        end_f_loop(f, f_thread)
    }
#endif
}

/* --------------------------- Source Hooks --------------------------- */

DEFINE_SOURCE(mass_source, c, t, dS, eqn)
{
    /* 연속 방정식 소스: 총 질량 제거 (Sm + Si) */
    real source = -C_UDMI(c, t, 2);
    dS[eqn] = 0.0; 
    return source;
}

DEFINE_SOURCE(species_salt_source, c, t, dS, eqn)
{
    /* Salt 종 수지 소스: Si 제거 */
    real source = -C_UDMI(c, t, 0);
    dS[eqn] = 0.0;
    return source;
}

DEFINE_SOURCE(x_mom_source, c, t, dS, eqn)
{
    /* 모멘텀 소스: S = -Sm * u */
    /* 수렴성 향상을 위해 Jacobian dS/du = -Sm 제공 */
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