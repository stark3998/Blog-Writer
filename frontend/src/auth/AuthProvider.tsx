import { useEffect, useRef, useState, createContext, useContext, type ReactNode } from "react";
import {
  PublicClientApplication,
  InteractionRequiredAuthError,
  type AccountInfo,
} from "@azure/msal-browser";
import { MsalProvider, useMsal, useIsAuthenticated } from "@azure/msal-react";
import { msalConfig, loginRequest, isAuthConfigured } from "./msalConfig";
import { setAccessTokenGetter } from "../services/api";

const msalInstance = new PublicClientApplication(msalConfig);

// Initialize MSAL — handles redirect promise on page load
msalInstance.initialize().then(() => {
  msalInstance.handleRedirectPromise().catch(console.error);
});

interface AuthContextValue {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: AccountInfo | null;
  login: () => Promise<void>;
  logout: () => void;
  getAccessToken: () => Promise<string>;
}

const AuthContext = createContext<AuthContextValue>({
  isAuthenticated: false,
  isLoading: true,
  user: null,
  login: async () => {},
  logout: () => {},
  getAccessToken: async () => "",
});

export function useAuth() {
  return useContext(AuthContext);
}

function AuthContextProvider({ children }: { children: ReactNode }) {
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const [isLoading, setIsLoading] = useState(true);

  const tokenGetterWired = useRef(false);

  useEffect(() => {
    // Brief delay to let MSAL finish initializing
    const timer = setTimeout(() => setIsLoading(false), 500);
    return () => clearTimeout(timer);
  }, []);

  // Wire the token getter into the API module once
  useEffect(() => {
    if (!tokenGetterWired.current) {
      setAccessTokenGetter(getAccessToken);
      tokenGetterWired.current = true;
    }
  });

  const user = accounts[0] ?? null;

  async function login() {
    try {
      await instance.loginPopup(loginRequest);
    } catch (err) {
      console.error("Login failed:", err);
    }
  }

  function logout() {
    instance.logoutPopup({ postLogoutRedirectUri: window.location.origin });
  }

  async function getAccessToken(): Promise<string> {
    if (!isAuthConfigured) return "";

    const account = accounts[0];
    if (!account) return "";

    try {
      const response = await instance.acquireTokenSilent({
        ...loginRequest,
        account,
      });
      return response.accessToken;
    } catch (err) {
      if (err instanceof InteractionRequiredAuthError) {
        const response = await instance.acquireTokenPopup(loginRequest);
        return response.accessToken;
      }
      console.error("Token acquisition failed:", err);
      return "";
    }
  }

  return (
    <AuthContext.Provider
      value={{ isAuthenticated, isLoading, user, login, logout, getAccessToken }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function AppAuthProvider({ children }: { children: ReactNode }) {
  // When auth is not configured, skip MSAL entirely
  if (!isAuthConfigured) {
    return (
      <AuthContext.Provider
        value={{
          isAuthenticated: true,
          isLoading: false,
          user: null,
          login: async () => {},
          logout: () => {},
          getAccessToken: async () => "",
        }}
      >
        {children}
      </AuthContext.Provider>
    );
  }

  return (
    <MsalProvider instance={msalInstance}>
      <AuthContextProvider>{children}</AuthContextProvider>
    </MsalProvider>
  );
}
