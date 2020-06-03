import numpy as np
import casadi as ca
from .integrator import Integrator
from .auxiliary import compute_qp_res

np_t = np.transpose

class OcpDims:
    def __init__(self, nx, nu, ng, ngN, N, M):
        """
        Define dimensions of an optimal control formulation

        Parameters
        ----------
        nx  : int
            number of states
        nu  : int
            number of inputs 
        ng  : int
            number of constraints
        ngN : int
            number of terminal constraints
        N  : int
            prediction horizon
        M  : int
            untightened prediction horizon
        """
        self.nx = nx
        self.nu = nu
        self.ng = ng
        self.ngN = ngN
        self.N = N
        self.M = M

class Ocp:
    def __init__(self, dims, x, u, lc_, lcN_, g_, gN_, fc_, T, tau, print_level = 1):
        """
        Define an optimal control formulation

        Parameters
        ----------
        dims : OcpDims 
            dimensions of the optimal control problem
        x    : CasADi MX
            CasADi symbolic variables representing the states
        u    : CasADi MX
            CasADi symbolic variables representing the inputs
        lc_  : CasADi expression 
            lc: R^{nx} x R^{nu} -> R (continuous-time Lagrange term)
        lcN_ : CasADi expression 
            lcN: R^{nx} -> R (Mayer term)
        g_   : CasADi expression 
            g: R^{n_x} x R^{n_u} -> R^{ng} (constraint function)
        gN_  : CasADi expression 
            gN: R^{nx} -> R^{ngN} (constraint function at t=T)
        fc_  : CasADi expression 
            fc: R^{nx} x R^{nu} -> R^{nx} (continuous time dynamics)
        T    : float 
            prediction horizon
        tau  : float 
            tightening factor
        print_level : int
            print level
        """

        self.tau = tau
        self.print_level = print_level

        # define CasADi functions
        lc = ca.Function('lc', [x,u], [lc_])
        lcN = ca.Function('lcN', [x], [lcN_])
        g = ca.Function('g', [x,u], [g_])
        gN = ca.Function('gN', [x], [gN_])
        fc = ca.Function('fc', [x, u], [fc_])

        self.lc = lc
        self.lcN = lcN
        self.g = g

        self.gN = gN
        self.fc = fc

        self.dims = dims
        NX = dims.nx
        NU = dims.nu
        NG = dims.ng
        NGN = dims.ngN
        N = dims.N
        M = dims.M

        Td = T/N

        # create integrator
        integrator = Integrator(x, u, fc_, Td)
        self.integrator = integrator

        # build OCP
        w=[]
        w0 = []
        lbw = []
        ubw = []
        c=[]
        lbc = []
        ubc = []
        Xk = ca.MX.sym('X0', NX, 1)
        w += [Xk]
        lbw += [np.zeros((NX,1))]
        ubw += [np.zeros((NX,1))]
        w0 += [np.zeros((NX,1))]
        f = 0
    
        # formulate the NLP
        for k in range(M):

            # new NLP variable for the control
            Uk = ca.MX.sym('U_' + str(k), NU, 1)

            # update variable list
            w   += [Uk]
            lbw += [-np.inf*np.ones((NU, 1))]
            ubw += [np.inf*np.ones((NU, 1))]
            w0  += [np.zeros((NU, 1))]

            # add cost contribution
            f = f + Td*lc(Xk, Uk)

            # add constraints
            c += [g(Xk, Uk)]
            lbc += [-np.inf*np.ones((NG, 1))]
            ubc += [np.zeros((NG, 1))]

            # integrate till the end of the interval
            Xk_end = integrator.xplus(Xk, Uk)

            # new NLP variable for state at end of interval
            Xk = ca.MX.sym('X_' + str(k+1), NX, 1)
            w   += [Xk]
            lbw += [-np.inf*np.ones((NX, 1))]
            ubw += [np.inf*np.ones((NX, 1))]
            w0  += [np.zeros((NX, 1))]

            # add equality constraint
            c   += [Xk_end-Xk]
            lbc += [np.zeros((NX, 1))]
            ubc += [np.zeros((NX, 1))]

        for k in range(M, N):

            # new NLP variable for the control
            Uk = ca.MX.sym('U_' + str(k), NU, 1)

            # compute barrier term
            barr_term = 0
            for i in range(NG):
                barr_term = barr_term -tau*np.log(-g(Xk, Uk)[i])

            f = f + Td*lc(Xk, Uk) + barr_term

            w   += [Uk]
            lbw += [-np.inf*np.ones((NU, 1))]
            ubw += [np.inf*np.ones((NU, 1))]
            w0  += [np.zeros((NU, 1))]

            # integrate till the end of the interval
            Xk_end = integrator.xplus(Xk, Uk)

            # new NLP variable for state at end of interval
            Xk = ca.MX.sym('X_' + str(k+1), NX, 1)
            w   += [Xk]
            lbw += [-np.inf*np.ones((NX, 1))]
            ubw += [np.inf*np.ones((NX, 1))]
            w0  += [np.zeros((NX, 1))]

            # add equality constraint
            c   += [Xk_end-Xk]
            lbc += [np.zeros((NX, 1))]
            ubc += [np.zeros((NX, 1))]

        if M == N:
            # compute barrier term
            barr_term = 0
            for i in range(NGN):
                barr_term = barr_term + -tau*np.log(-gN(Xk)[i])

            f = f + lcN(Xk) + barr_term
        else:

            f = f + lcN(Xk)

            # add constraints
            c += [gN(Xk)]
            lbc += [np.zeros((NGN, 1))]
            ubc += [np.zeros((NGN, 1))]

        c = ca.vertcat(*c)
        w = ca.vertcat(*w)
        
        # convert lists to numpy arrays
        lbw_a = np.vstack(lbw)
            
        self._lbw = np.vstack(lbw)
        self._ubw = np.vstack(ubw)

        self._lbc = np.vstack(lbc)
        self._ubc = np.vstack(ubc)

        self._w0 = np.vstack(w0)

        # create an NLP solver
        prob = {'f': f, 'x': w, 'g': c}
        # opts = {'ipopt': {'print_level': 2}}
        opts = {}
        self.nlp_solver = ca.nlpsol('solver', 'ipopt', prob, opts);

        #----------------------------------------------------------------------
        #                       partially tightened RTI 
        #----------------------------------------------------------------------

        # define CasADi functions for linearization

        # dynamics
        jac_x_f = ca.Function('jac_x_f', [integrator.x, integrator.u], \
            [ca.jacobian(integrator.xplus_expr, integrator.x)])
        self.jac_x_f = jac_x_f

        jac_u_f = ca.Function('jac_u_f', [integrator.x, integrator.u], \
            [ca.jacobian(integrator.xplus_expr, integrator.u)])
        self.jac_u_f = jac_u_f

        # cost
        jac_x_l = ca.Function('jac_x_l', [x, u], \
            [ca.jacobian(Td*lc_, x)])
        self.jac_x_l = jac_x_l

        jac_u_l = ca.Function('jac_u_l', [x, u], \
            [ca.jacobian(Td*lc_, u)])
        self.jac_u_l = jac_u_l

        jac_xx_l = ca.Function('jac_xx_l', [x, u], \
            [ca.hessian(Td*lc_, x)[0]])
        self.jac_xx_l = jac_xx_l

        jac_uu_l = ca.Function('jac_uu_l', [x, u], \
            [ca.hessian(Td*lc_, u)[0]])
        self.jac_uu_l = jac_uu_l

        jac_xx_lN = ca.Function('jac_xx_lN', [x], \
            [ca.hessian(lcN_, x)[0]])
        self.jac_xx_lN = jac_xx_lN

        jac_x_lN = ca.Function('jac_x_lN', [x], \
            [ca.jacobian(lcN_, x)])
        self.jac_x_lN = jac_x_lN

        # constraints
        jac_x_g = ca.Function('jac_x_g', [x, u], \
            [ca.jacobian(g_, x)])
        self.jac_x_g = jac_x_g

        jac_u_g = ca.Function('jac_u_g', [x, u], \
            [ca.jacobian(g_, u)])
        self.jac_u_g = jac_u_g

        jac_x_gN = ca.Function('jac_x_gN', [x], \
            [ca.jacobian(gN_, x)])
        self.jac_x_gN = jac_x_gN

        # these are the primal-dual iterates of the partially tightened RTI
        self.x = []
        self.u = []
        self.lam = []
        self.t = []
        self.nu = []

        t_init = 1
        nu_init = 1

        for i in range(N):
            self.x.append(np.zeros((NX,1)))
            self.u.append(np.zeros((NU,1)))
            self.lam.append(np.zeros((NX,1)))
            self.t.append(t_init*np.ones((NG,1)))
            self.nu.append(nu_init*np.ones((NG,1)))

        self.x.append(np.zeros((NX,1)))
        self.lam.append(np.zeros((NX,1)))
        self.t.append(t_init*np.ones((NGN,1)))
        self.nu.append(nu_init*np.ones((NGN,1)))

        # these are the variables associated with the linearized problem
        # - matrices
        self.A = []
        self.B = []
        self.C = []
        self.D = []
        self.Hxx = []
        self.Huu = []
        self.Hxu = []
        self.Hxx_t = []
        self.Huu_t = []
        self.Hxu_t = []

        for i in range(N):
            self.A.append(np.zeros((NX,NX)))
            self.B.append(np.zeros((NX,NU)))
            self.C.append(np.zeros((NG,NX)))
            self.D.append(np.zeros((NG,NU)))
            self.Hxx.append(np.zeros((NX,NX)))
            self.Huu.append(np.zeros((NU,NU)))
            self.Hxu.append(np.zeros((NU,NX)))
            self.Hxx_t.append(np.zeros((NX,NX)))
            self.Huu_t.append(np.zeros((NU,NU)))
            self.Hxu_t.append(np.zeros((NU,NX)))

        self.C.append(np.zeros((NGN,NX)))
        self.D.append(np.zeros((NGN,NU)))
        self.Hxx.append(np.zeros((NX,NX)))
        self.Hxx_t.append(np.zeros((NX,NX)))

        # - vectors (residuals)
        self.r_lam = []
        self.r_x = []
        self.r_x_t = []
        self.r_u = []
        self.r_u_t = []
        self.r_nu = []
        self.e = []

        for i in range(N):
            self.r_lam.append(np.zeros((NX,1)))
            self.r_x.append(np.zeros((NX,1)))
            self.r_x_t.append(np.zeros((NX,1)))
            self.r_u.append(np.zeros((NU,1)))
            self.r_u_t.append(np.zeros((NU,1)))
            self.r_nu.append(np.zeros((NG,1)))
            self.e.append(np.zeros((NG,1)))

        self.r_lam.append(np.zeros((NX,1)))
        self.r_x.append(np.zeros((NX,1)))
        self.r_x_t.append(np.zeros((NX,1)))
        self.r_u.append(np.zeros((NU,1)))
        self.r_nu.append(np.zeros((NG,1)))
        self.e.append(np.zeros((NG,1)))

        # - vectors (QP residuals)
        self.r_lam_qp = []
        self.r_x_qp = []
        self.r_u_qp = []
        self.r_nu_qp = []
        self.e_qp = []

        for i in range(N):
            self.r_lam_qp.append(np.zeros((NX,1)))
            self.r_x_qp.append(np.zeros((NX,1)))
            self.r_u_qp.append(np.zeros((NU,1)))
            self.r_nu_qp.append(np.zeros((NG,1)))
            self.e_qp.append(np.zeros((NG,1)))

        self.r_lam_qp.append(np.zeros((NX,1)))
        self.r_x_qp.append(np.zeros((NX,1)))
        self.r_u_qp.append(np.zeros((NU,1)))
        self.r_nu_qp.append(np.zeros((NG,1)))
        self.e_qp.append(np.zeros((NG,1)))

        # these are the variables associated with the Riccati recursion 
        self.P = []
        self.p = []

        for i in range(N+1):
            self.p.append(np.zeros((NX,1)))
            self.P.append(np.zeros((NX,NX)))

        # solution of linearized problem

        self.du = []
        self.dx = []
        self.dlam = []
        self.dnu = []
        self.dt = []

        for i in range(N):
            self.du.append(np.zeros((NU,1)))
            self.dx.append(np.zeros((NX,1)))
            self.dt.append(np.zeros((NG,1)))
            self.dlam.append(np.zeros((NX,1)))
            self.dnu.append(np.zeros((NG,1)))

        self.dlam.append(np.zeros((NX,1)))
        self.dx.append(np.zeros((NX,1)))
        self.dt.append(np.zeros((NGN,1)))
        self.dnu.append(np.zeros((NGN,1)))

        self.x0 = np.zeros((NX,1))


    def update_x0(self, x0):
        """
        Update the initial condition in the OCP

        Parameters:
        -----------
        x0 : numpy array
            new value of x0
        """
        for i in range(self.dims.nx):
            self._lbw[i] = x0[i]
            self._ubw[i] = x0[i]
        self.x0 = x0

    def eval(self):
        """
        Compute exact solution to OCP
        """
        sol = self.nlp_solver(x0=self._w0, lbx=self._lbw, ubx=self._ubw,\
            lbg=self._lbc, ubg=self._ubc)

        return sol

    def linearize(self):
        N = self.dims.N
        M = self.dims.M
        ng = self.dims.ng
        ngN = self.dims.ngN

        for i in range(0,N):
            x = self.x[i]
            u = self.u[i]

            # matrices
            self.A[i] = self.jac_x_f(x,u).full()
            self.B[i] = self.jac_u_f(x,u).full()
            # TODO(andrea): add Hessian contributions from dynamics and constraints?
            self.Hxx[i] = self.jac_xx_l(x,u).full()
            self.Huu[i] = self.jac_uu_l(x,u).full()
            self.C[i] = self.jac_x_g(x,u).full()
            self.D[i] = self.jac_u_g(x,u).full()

        x = self.x[N]
        self.Hxx[N] = self.jac_xx_lN(x).full()
        self.C[N] = self.jac_x_gN(x).full()

        # vectors
        i = 0

        x   = self.x[i]
        u   = self.u[i]
        lam = self.lam[i]
        lam_next = self.lam[i+1]
        nu  = self.nu[i]
        t  = self.t[i]

        self.r_lam[i] = - x + self.x0

        if ng > 0:
            self.r_x[i] = np_t(self.jac_x_l(x,u).full()) + \
                np.dot(np_t(self.jac_x_f(x,u).full()), lam_next) - lam + \
                np.dot(np_t(self.jac_x_g(x,u).full()), nu)

            self.r_u[i] = np_t(self.jac_u_l(x,u).full()) + \
                    np.dot(np_t(self.jac_u_f(x,u).full()), lam_next)  + \
                    np.dot(np_t(self.jac_u_g(x,u).full()), nu)

            self.r_nu[i] = self.g(x,u).full() + t 

            if i >= M:
                self.e[i] = np.dot(np.diagflat(t), nu) - \
                    self.tau*np.ones((self.dims.ng,1)) 
        else:
            self.r_x[i] = np_t(self.jac_x_l(x,u).full()) + \
                np.dot(np_t(self.jac_x_f(x,u).full()), lam_next) - lam

            self.r_u[i] = np_t(self.jac_u_l(x,u).full()) + \
                    np.dot(np_t(self.jac_u_f(x,u).full()), lam_next)

        for i in range(1,N):
            x   = self.x[i]
            u   = self.u[i]
            x_prev = self.x[i-1]
            u_prev = self.u[i-1]
            lam = self.lam[i]
            lam_next = self.lam[i+1]
            nu  = self.nu[i]
            t  = self.t[i]

            self.r_lam[i] = -x + self.integrator.eval(x_prev, u_prev)

            if ng > 0:
                self.r_x[i] = np_t(self.jac_x_l(x,u).full()) + \
                    np.dot(np_t(self.jac_x_f(x,u).full()), lam_next) - lam + \
                    np.dot(np_t(self.jac_x_g(x,u).full()), nu)

                self.r_u[i] = np_t(self.jac_u_l(x,u).full()) + \
                    np.dot(np_t(self.jac_u_f(x,u).full()), lam_next)  + \
                    np.dot(np_t(self.jac_u_g(x,u).full()), nu)

                self.r_nu[i] = self.g(x,u).full() + self.t[i] 

                if i >= M:
                    self.e[i] = np.dot(np.diagflat(t), nu) - \
                        self.tau*np.ones((self.dims.ng,1)) 
            else:
                self.r_x[i] = np_t(self.jac_x_l(x,u).full()) + \
                    np.dot(np_t(self.jac_x_f(x,u).full()), lam_next) - lam

                self.r_u[i] = np_t(self.jac_u_l(x,u).full()) + \
                    np.dot(np_t(self.jac_u_f(x,u).full()), lam_next)

        i = N
        x   = self.x[i]
        x_prev = self.x[i-1]
        u_prev = self.u[i-1]
        lam = self.lam[i]
        nu  = self.nu[i]
        t  = self.t[i]

        self.r_lam[i] = -x + self.integrator.eval(x_prev, u_prev)

        if ngN > 0:
            self.r_x[i] = np_t(self.jac_x_lN(x).full()) + \
                 - lam + \
                np.dot(np_t(self.jac_x_gN(x).full()), nu)

            self.r_nu[i] = self.gN(x).full() + t

            if i >= M:
                self.e[i] = np.dot(np.diagflat(t), nu) - \
                    self.tau*np.ones((self.dims.ngN,1)) 
        else:
            self.r_x[i] = np_t(self.jac_x_lN(x).full()) - lam

        if self.print_level > 0:
            # compute and print residuals
            r_lam = np.linalg.norm(np.vstack(self.r_lam))
            r_x = np.linalg.norm(np.vstack(self.r_x))
            r_u = np.linalg.norm(np.vstack(self.r_u))
            r_nu = np.linalg.norm(np.vstack(self.r_nu))
            e = np.linalg.norm(np.vstack(self.e))
            print('r_lam: {:.1e}, r_x: {:.1e}, r_u: {:.1e}, r_nu: '
            '{:.1e}, e: {:.1e}'.format(r_lam, r_x, r_u, r_nu, e))

        return


    def eliminate_nu_lam(self):
        """
        Eliminate slack and inequality multipliers.
        """

        N = self.dims.N
        M = self.dims.M
        for i in range(M, N):
            VT_inv = np.diagflat(np.divide(self.nu[i], self.t[i]))
            C = self.C[i]
            D = self.D[i]

            # matrices
            self.Hxx_t[i] = self.Hxx[i] + np.dot(np.dot(np_t(C), VT_inv), C)
            self.Huu_t[i] = self.Huu[i] + np.dot(np.dot(np_t(D), VT_inv), D)
            self.Hxu_t[i] = self.Hxu[i] + np.dot(np.dot(np_t(D), VT_inv), C)

            # vectors
            self.r_u_t[i] = self.r_u[i] + \
                np.dot(np.dot(np_t(D), VT_inv), self.r_nu[i]) - \
                np.dot(np_t(D), np.dot(np.diagflat(np.divide(np.ones((self.dims.ng, 1)), self.t[i])), self.e[i]))

        VT_inv = np.diagflat(np.divide(self.nu[N], self.t[N]))
        C = self.C[N]
        self.Hxx_t[N] = self.Hxx[N] + np.dot(np.dot(np_t(C), VT_inv), C)
        t = self.t[N]

        if M < N:
            if self.dims.ngN > 0:
                self.r_x_t[N] = self.r_x[N] + \
                np.dot(np.dot(np_t(C), VT_inv), self.r_nu[N]) - \
                np.dot(np_t(C), np.dot(np.diagflat(np.divide(np.ones((self.dims.ngN, 1)), t)), \
                self.e[N]))
            else:
                self.r_x_t[N] = self.r_x[N]

        return

    def update_vectors_stage_M(self):
        """
        Eliminate multiplier and state associated with stage M and update the corresponding
        right hand sides.
        """

        M = self.dims.M
        r_lam_M = self.r_lam[M]

        M = self.dims.M
        self.r_u_t[M] = self.r_u_t[M] + np.dot(self.Hxu[M], r_lam_M)
        self.r_lam[M+1] = self.r_lam[M+1] + np.dot(self.A[M], r_lam_M)

    def backward_riccati(self):
        """
        Perform backward Riccati recursion for stages N to M.
        """

        N = self.dims.N
        M = self.dims.M
        self.P[N] = self.Hxx_t[N]
        self.p[N] = self.r_x_t[N]
        for i in range(N-1,M-1,-1):
            # matrix recursion
            A = self.A[i]
            B = self.B[i]
            Q = self.Hxx_t[i]
            R = self.Huu_t[i]
            S = self.Hxu_t[i]
            P = self.P[i+1]

            Sigma = -np.dot(np_t(S) + np.dot(np.dot(np_t(A), P), B), \
                np.linalg.inv(R + np.dot(np.dot(np_t(B), P), B)))

            self.P[i] = Q + np.dot(np.dot(np_t(A), P), A) + \
                    np.dot(np.dot(np.dot(Sigma, S + np_t(B)), P), A)

            # vector recursion
            p = self.p[i+1]
            r_x_t = self.r_x_t[i]
            r_lam = self.r_lam[i+1]
            self.p[i] = r_x_t + np.dot(np.transpose(A), np.dot(P, r_lam) + p) \
                + np.dot(Sigma, self.r_u_t[i] + np.dot(np.transpose(B), np.dot(P, r_lam) + p))

        return

    def forward_riccati(self):
        """
        Perform forward Riccati recursion for stages M to N.
        """

        N = self.dims.N
        M = self.dims.M

        A = self.A[M]
        B = self.B[M]
        Q = self.Hxx_t[M]
        R = self.Huu_t[M]
        S = self.Hxu_t[M]
        P = self.P[M+1]
        p = self.p[M+1]
        P_i = self.P[M]
        p_i = self.p[M]
        r_u_t = self.r_u_t[M]
        r_lam = self.r_lam[M+1]
        Gamma = np.linalg.inv(R + np.dot(np.dot(np_t(B), P), B))
        Kappa = -np.dot(Gamma, S + np.dot(np_t(B), np.dot(P, A)))
        kappa = -np.dot(Gamma, r_u_t + np.dot(np_t(B), np.dot(P, r_lam) + p)) 

        self.dx[M] = self.r_lam[M] 
        self.du[M] = np.dot(Kappa, self.dx[M]) + kappa
        self.dlam[M] = np.dot(P_i, self.dx[M]) + p_i

        self.dx[M+1] = np.dot(A, self.dx[M]) + np.dot(B, self.du[M]) + self.r_lam[M+1] 

        for i in range(M+1, N):
            A = self.A[i]
            B = self.B[i]
            Q = self.Hxx_t[i]
            R = self.Huu_t[i]
            S = self.Hxu_t[i]
            P = self.P[i+1]
            p = self.p[i+1]
            P_i = self.P[i]
            p_i = self.p[i]
            Gamma = np.linalg.inv(R + np.dot(np.dot(np_t(B), P), B))
            Kappa = -np.dot(Gamma, S + np.dot( np_t(B), np.dot(P, A)))
            kappa = -np.dot(Gamma, self.r_u_t[i] + np.dot(np_t(B), np.dot(P, self.r_lam[i+1]) + p)) 

            self.dx[i] = np.dot(A, self.dx[i-1]) + np.dot(B, self.du[i-1]) + self.r_lam[i]
            self.du[i] = np.dot(Kappa, self.dx[i]) + kappa
            self.dlam[i] = np.dot(P_i, self.dx[i]) + p_i

        if M < N:
            i = N
            P_i = self.P[i]
            p_i = self.p[i]
            self.dx[i] = np.dot(A, self.dx[i-1]) + np.dot(B, self.du[i-1]) + self.r_lam[i]
            self.dlam[i] = np.dot(P_i, self.dx[i]) + p_i

        return

    def expand_solution(self):
        """
        Expand solution to space of slacks and inequality multipliers.
        """

        N = self.dims.N
        M = self.dims.M

        for i in range(M, N):
            NG = self.dims.ng
            VT_inv = np.diagflat(np.divide(self.nu[i], self.t[i]))
            C = self.C[i]
            D = self.D[i]
            r_nu = self.r_nu[i]
            nu = self.nu[i]
            dx = self.dx[i]
            du = self.du[i]
            e = self.e[i]
            t = self.t[i]

            self.dnu[i] = np.dot(VT_inv, r_nu - \
                np.dot(np.diagflat(np.divide(np.ones((NG,1)), nu)), e) + \
                np.dot(C, dx) + np.dot(D, du))

            self.dt[i] = -np.dot(np.diagflat(np.divide(np.ones((NG,1)), self.nu[i])), \
                np.dot(np.diagflat(t), self.dnu[i]) + e)

        if M < N:
            if self.dims.ngN > 0:
                i = N
                NGN = self.dims.ngN
                VT_inv = np.diagflat(np.divide(self.nu[i], self.t[i]))
                C = self.C[i]
                D = self.D[i]
                r_nu = self.r_nu[i]
                nu = self.nu[i]
                dx = self.dx[i]
                e = self.e[i]
                t = self.t[i]

                self.dnu[i] = np.dot(np.diagflat(np.divide(nu, t)), r_nu - \
                    np.dot(np.diagflat(np.divide(np.ones((NGN,1)), nu)), e) + \
                    np.dot(C, dx))

                self.dt[i] = -np.dot(np.diagflat(np.divide(np.ones((NGN,1)), nu)), \
                    np.dot(np.diagflat(t), dnu) + e)

    def solve_reduced_qp(self):
        """
        Solve reduced QP with horizon M.
        """

    def primal_dual_step(self):
        """
        Carry out primal-dual step using current (delta) solution.
        """

        N = self.dims.N

        alpha_nu = np.min(np.abs(np.divide(-np.vstack(self.nu), \
            np.vstack(self.dnu))))
        alpha_t = np.min(np.abs(np.divide(-np.vstack(self.t), \
            np.vstack(self.dt))))
        alpha = np.min([alpha_t, alpha_nu, 1.0])
        alpha = 0.9995*alpha

        if self.print_level > 0:
            # compute and print step size
            dlam = np.linalg.norm(np.vstack(self.dlam))
            dx = np.linalg.norm(np.vstack(self.dx))
            du = np.linalg.norm(np.vstack(self.du))
            dnu = np.linalg.norm(np.vstack(self.dnu))
            dt = np.linalg.norm(np.vstack(self.dt))
            print('alpha: {:.1e}, dlam: {:.1e}, dx: {:.1e}, du: {:.1e}'
            ' dnu: {:.1e}, dt: {:.1e}'.format(alpha, dlam, dx, du, dnu, dt))

        for i in range(N):
            self.x[i] = self.x[i] + alpha*self.dx[i]
            self.u[i] = self.u[i] + alpha*self.du[i]
            self.lam[i] = self.lam[i] + alpha*self.dlam[i]
            self.t[i] = self.t[i] + alpha*self.dt[i]
            self.nu[i] = self.nu[i] + alpha*self.dnu[i]

        i = N
        self.x[i] = self.x[i] + alpha*self.dx[i]
        self.lam[i] = self.lam[i] + alpha*self.dlam[i]
        self.t[i] = self.t[i] + alpha*self.dt[i]
        self.nu[i] = self.nu[i] + alpha*self.dnu[i]

    def pt_rti(self):
        """
        Perform a full partially tightened real-time iteration.
        """
        ngN = self.dims.ngN
        ng = self.dims.ng
        N = self.dims.N

        self.linearize()
        if ng > 0 or ngN > 0:
            self.eliminate_nu_lam()
        else:
            for i in range(N):
                self.r_x_t[i] = self.r_x[i]
                self.r_u_t[i] = self.r_u[i]
                self.Hxx_t[i] = self.Hxx[i]
                self.Huu_t[i] = self.Huu[i]
                self.Hxu_t[i] = self.Hxu[i]
            self.r_x_t[N] = self.r_x[N]
            self.Hxx_t[N] = self.Hxx[N]
        self.update_vectors_stage_M()
        self.backward_riccati()
        self.forward_riccati()
        if ng > 0 or ngN > 0:
            self.expand_solution()
        compute_qp_res(self)
        self.primal_dual_step()
        self.linearize()
