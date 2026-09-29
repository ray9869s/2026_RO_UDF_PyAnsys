#include "udf.h"
#include <math.h>
#include <string.h>

/*
   260929_RO_UDF.c

   2D membrane source for the RO pilot. Do not use this file in a 3D case.
   Production 3D physics stays in 260822_RO_UDF.c, which is not modified.

   Coordinates:
     2D: x = flow, y = wall-normal, membranes at y = +/- H/2.
     3D campaign: x = flow, z = wall-normal.
   Component 1 is therefore y here. It is not a rename of the 3D z index.

   Both wall_top_mem and wall_bottom_mem use the same source. The face-area
   vector sign is removed by the absolute normal projection, so the top and
   bottom walls do not need opposite source signs.

   Inlet: plane Poiseuille in y, normalised by the discrete factor G so the
   edge-length-weighted mean equals U_TARGET. Same construction as the 3D
   profile, with y in place of z.
*/

#if RP_3D
#error "260929_RO_UDF.c is a 2D membrane source. Use 260822_RO_UDF.c for 3D."
#endif

#define SALT_YI_INDEX   0

#define MEMB_THREAD_BASE_NAME_TOP     "wall_top_mem"
#define MEMB_THREAD_BASE_NAME_BOTTOM  "wall_bottom_mem"
#define INLET_THREAD_BASE_NAME        "inlet"

#define MW_SALT         0.05844
#define C_INLET_REF     597.8268309
#define RHO_REF         998.20
#define MS_TO_LMH       3600000.0
#define D_SALT          2.0e-9

#define RAMP_ITER_1       50
#define RAMP_ITER_2      100
#define RAMP_ITER_3      150
#define RAMP_FACTOR_1    0.2
#define RAMP_FACTOR_2    0.5
#define RAMP_FACTOR_3    0.8
#define RAMP_FACTOR_FULL 1.0

#define MIN_SOURCE_DENOMINATOR 1.0e-20

#ifndef RO_ANALYTIC_CWALL
#define RO_ANALYTIC_CWALL 1
#endif

/* Case-local copy is patched. Master values are the campaign channel. */
#define INLET_Y_BOTTOM            (-0.385e-3)
#define CHANNEL_HEIGHT            ( 0.770e-3)
#define U_TARGET                  0.2
#define INLET_G_MIN               0.9999
#define INLET_G_MAX               1.02

static real A_perm = 2.50e-12;
static real B_perm = 2.50e-8;
static real kappa  = 4958.0;
static real p_perm = 101325.0;

enum {
    UDM_SI = 0,
    UDM_TOTAL_S = 1,
    UDM_XMOM = 2,
    UDM_YMOM = 3,
    UDM_ZMOM = 4,          /* reserved so indices match 260822; not written */
    UDM_STRAIN_RATE = 5,
    UDM_JW = 6,
    UDM_CM = 7,
    UDM_LMH = 8,
    UDM_CP = 9,
    UDM_SALT_FLUX = 10,
    UDM_AREA = 11,
    UDM_Y1 = 12,
    UDM_COUNT = 13
};

static int inlet_G_ready = 0;
static real inlet_G = 1.0;


static int ro_udm_available(void)
{
    return N_UDM >= UDM_COUNT;
}


static real source_ramp(void)
{
    int iter = N_ITER;

    if (iter < RAMP_ITER_1) return RAMP_FACTOR_1;
    if (iter < RAMP_ITER_2) return RAMP_FACTOR_2;
    if (iter < RAMP_ITER_3) return RAMP_FACTOR_3;
    return RAMP_FACTOR_FULL;
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


/* Wall distance from the area vector. ND_ND keeps this valid in 2D. */
static real ro_wall_distance(real xc[], real xf[], real area[])
{
    real dot;
    real magnitude;
    int dimension;

    magnitude = NV_MAG(area);
    if (magnitude <= 0.0) {
        return 0.0;
    }
    dot = 0.0;
    for (dimension = 0; dimension < ND_ND; ++dimension) {
        dot += (xc[dimension] - xf[dimension]) * area[dimension];
    }
    return fabs(dot) / magnitude;
}


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


#if ND_ND != 2
#error "260929 inlet profile expects ND_ND == 2, with y as component 1."
#endif

static real inlet_poiseuille_shape(real y_coord)
{
    real eta;

    eta = (y_coord - INLET_Y_BOTTOM) / CHANNEL_HEIGHT;
    if (eta < 0.0) {
        eta = 0.0;
    }
    if (eta > 1.0) {
        eta = 1.0;
    }
    return 6.0 * eta * (1.0 - eta);
}


static void ensure_inlet_G(void)
{
    Domain *domain;
    Thread *face_thread;
    face_t face;
    real centroid[ND_ND];
    real area[ND_ND];
    real shape_area;
    real area_sum;
    real shape;
    real length;

    if (inlet_G_ready) {
        return;
    }
    shape_area = 0.0;
    area_sum = 0.0;
    domain = Get_Domain(1);
    thread_loop_f(face_thread, domain) {
        if (!is_inlet_face_thread(face_thread)) {
            continue;
        }
        begin_f_loop(face, face_thread) {
            F_CENTROID(centroid, face, face_thread);
            shape = inlet_poiseuille_shape(centroid[1]);
            F_AREA(area, face, face_thread);
            length = NV_MAG(area);
            shape_area += shape * length;
            area_sum += length;
        }
        end_f_loop(face, face_thread)
    }
    if (area_sum > 0.0) {
        inlet_G = shape_area / area_sum;
    }
    else {
        inlet_G = 0.0;
    }
    if (!(inlet_G > 0.0) || inlet_G != inlet_G ||
        inlet_G < INLET_G_MIN || inlet_G > INLET_G_MAX) {
        Message0(
            "WARNING: RO_UDF_INLET_G_OUT_OF_RANGE G=%.6g; falling back to G=1\n",
            inlet_G
        );
        inlet_G = 1.0;
    }
    else {
        Message0("RO_UDF_INLET_PROFILE_G=%.12g\n", inlet_G);
    }
    inlet_G_ready = 1;
}


DEFINE_EXECUTE_ON_LOADING(RO_UDF_on_loading, libname)
{
    Message("\nRO 2D membrane UDF loaded from library: %s\n", libname);
    Message("Required user-defined memory locations: %d\n", UDM_COUNT);
    Message("Membrane walls: %s, %s\n",
            MEMB_THREAD_BASE_NAME_TOP, MEMB_THREAD_BASE_NAME_BOTTOM);
    Message("Wall-normal axis is y. RO_ANALYTIC_CWALL = %d. D_SALT = %.6g\n",
            RO_ANALYTIC_CWALL, D_SALT);
}


DEFINE_INIT(RO_UDF_init, domain)
{
    inlet_G_ready = 0;
    inlet_G = 1.0;
}


DEFINE_ADJUST(RO_membrane_adjust, domain)
{
#if !RP_HOST
    Thread *face_thread;
    Thread *cell_thread;
    face_t face;
    cell_t cell;
    real area[ND_ND];
    real face_centroid[ND_ND];
    real cell_centroid[ND_ND];
    real area_mag;
    real cell_volume;
    real y1;
    real ramp;
    real p_operating;
    real pressure_abs;
    real pressure_drop;
    real mass_fraction;
    real concentration;
    real suction;
    real discriminant;
    real water_flux;
    real salt_flux;
    real salt_mass_flux;
    real water_sink;
    real salt_sink;
    real total_sink;
    int cp_failed;
    real cp_face;

    if (!ro_udm_available()) {
        return;
    }

    ramp = source_ramp();
    Message("RO2D_SOURCE_RAMP iter=%d factor=%.6g\n", N_ITER, ramp);
    p_operating = RP_Get_Real("operating-pressure");

    thread_loop_c(cell_thread, domain) {
        begin_c_loop(cell, cell_thread) {
            C_UDMI(cell, cell_thread, UDM_SI) = 0.0;
            C_UDMI(cell, cell_thread, UDM_TOTAL_S) = 0.0;
            C_UDMI(cell, cell_thread, UDM_XMOM) = 0.0;
            C_UDMI(cell, cell_thread, UDM_YMOM) = 0.0;
        }
        end_c_loop(cell, cell_thread)
    }

    thread_loop_f(face_thread, domain) {
        if (!is_membrane_wall_thread(face_thread)) {
            continue;
        }
        begin_f_loop(face, face_thread) {
            cell = F_C0(face, face_thread);
            cell_thread = THREAD_T0(face_thread);
            F_AREA(area, face, face_thread);
            area_mag = NV_MAG(area);
            cell_volume = C_VOLUME(cell, cell_thread);
            if (area_mag <= 0.0 || cell_volume <= 0.0) {
                continue;
            }

            pressure_abs = C_P(cell, cell_thread) + p_operating;
            pressure_drop = pressure_abs - p_perm;
            mass_fraction = C_YI(cell, cell_thread, SALT_YI_INDEX);
            mass_fraction = MAX(0.0, MIN(mass_fraction, 1.0));
            concentration = RHO_REF * mass_fraction / MW_SALT;

            F_CENTROID(face_centroid, face, face_thread);
            C_CENTROID(cell_centroid, cell, cell_thread);
            y1 = ro_wall_distance(cell_centroid, face_centroid, area);

            suction = A_perm * (pressure_drop - kappa * concentration);
            discriminant = (suction + B_perm) * (suction + B_perm)
                         + 4.0 * A_perm * B_perm * kappa * concentration;
            water_flux = 0.5 * (suction - B_perm + sqrt(MAX(discriminant, 0.0)));
            water_flux = MAX(water_flux, 0.0);
            if (water_flux + B_perm > MIN_SOURCE_DENOMINATOR) {
                salt_flux = (B_perm * concentration * water_flux)
                          / (water_flux + B_perm);
            }
            else {
                salt_flux = 0.0;
            }
            salt_flux = MAX(salt_flux, 0.0);

#if RO_ANALYTIC_CWALL
            concentration = ro_cwall_from_film(concentration, water_flux, y1);
            suction = A_perm * (pressure_drop - kappa * concentration);
            discriminant = (suction + B_perm) * (suction + B_perm)
                         + 4.0 * A_perm * B_perm * kappa * concentration;
            water_flux = 0.5 * (suction - B_perm + sqrt(MAX(discriminant, 0.0)));
            water_flux = MAX(water_flux, 0.0);
            if (water_flux + B_perm > MIN_SOURCE_DENOMINATOR) {
                salt_flux = (B_perm * concentration * water_flux)
                          / (water_flux + B_perm);
            }
            else {
                salt_flux = 0.0;
            }
            salt_flux = MAX(salt_flux, 0.0);
#endif
            salt_mass_flux = salt_flux * MW_SALT;
            cp_face = ro_compute_cp(concentration, water_flux, &cp_failed);

            /* One membrane edge per boundary cell on this mesh: store the value. */
            C_UDMI(cell, cell_thread, UDM_JW) = water_flux;
            C_UDMI(cell, cell_thread, UDM_CM) = concentration;
            C_UDMI(cell, cell_thread, UDM_LMH) = water_flux * MS_TO_LMH;
            C_UDMI(cell, cell_thread, UDM_CP) = cp_face;
            C_UDMI(cell, cell_thread, UDM_SALT_FLUX) = salt_mass_flux;
            C_UDMI(cell, cell_thread, UDM_AREA) = area_mag;
            C_UDMI(cell, cell_thread, UDM_Y1) = y1;

            water_flux *= ramp;
            salt_mass_flux *= ramp;
            water_sink = water_flux * area_mag * RHO_REF / cell_volume;
            salt_sink = salt_mass_flux * area_mag / cell_volume;
            total_sink = water_sink + salt_sink;
            C_UDMI(cell, cell_thread, UDM_SI) += salt_sink;
            C_UDMI(cell, cell_thread, UDM_TOTAL_S) += total_sink;
            C_UDMI(cell, cell_thread, UDM_XMOM) += total_sink;
            C_UDMI(cell, cell_thread, UDM_YMOM) += total_sink;
        }
        end_f_loop(face, face_thread)
    }
#endif
}


DEFINE_SOURCE(mass_source, cell, thread, dS, eqn)
{
    dS[eqn] = 0.0;
    if (!ro_udm_available()) {
        return 0.0;
    }
    return -C_UDMI(cell, thread, UDM_TOTAL_S);
}


DEFINE_SOURCE(species_salt_source, cell, thread, dS, eqn)
{
    dS[eqn] = 0.0;
    if (!ro_udm_available()) {
        return 0.0;
    }
    return -C_UDMI(cell, thread, UDM_SI);
}


DEFINE_SOURCE(x_mom_source, cell, thread, dS, eqn)
{
    real coefficient;

    dS[eqn] = 0.0;
    if (!ro_udm_available()) {
        return 0.0;
    }
    coefficient = C_UDMI(cell, thread, UDM_XMOM);
    dS[eqn] = -coefficient;
    return -coefficient * C_U(cell, thread);
}


DEFINE_SOURCE(y_mom_source, cell, thread, dS, eqn)
{
    real coefficient;

    dS[eqn] = 0.0;
    if (!ro_udm_available()) {
        return 0.0;
    }
    coefficient = C_UDMI(cell, thread, UDM_YMOM);
    dS[eqn] = -coefficient;
    return -coefficient * C_V(cell, thread);
}


DEFINE_PROFILE(inlet_x_velocity_profile, thread, position)
{
#if !RP_HOST
    face_t face;
    real centroid[ND_ND];

    ensure_inlet_G();
    begin_f_loop(face, thread) {
        F_CENTROID(centroid, face, thread);
        F_PROFILE(face, thread, position) =
            U_TARGET * inlet_poiseuille_shape(centroid[1]) / inlet_G;
    }
    end_f_loop(face, thread)
#endif
}


DEFINE_ON_DEMAND(probe_inlet_profile)
{
#if !RP_HOST
    ensure_inlet_G();
    Message0("=== RO_UDF 2D inlet profile ===\n");
    Message0("  U_TARGET = %.6g m/s\n", U_TARGET);
    Message0("  INLET_Y_BOTTOM = %.6g m\n", INLET_Y_BOTTOM);
    Message0("  CHANNEL_HEIGHT = %.6g m\n", CHANNEL_HEIGHT);
    Message0("  wall-normal component index = 1 (y)\n");
#endif
}
