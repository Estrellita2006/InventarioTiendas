from typing import Optional
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from fastapi import Form


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
print("URL CARGADA:", DATABASE_URL)

db_pool = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    try:
        db_pool = ThreadedConnectionPool(1, 10, DATABASE_URL)
        print("Conexión exitosa a la base de datos Neon")
        yield
    finally:
        if db_pool:
            db_pool.closeall()
            print("Conexión a la base de datos cerrada")

app = FastAPI(
    title="API de Inventario - Neon Cloud",
    description="Backend completo con CRUD para Usuarios, Proveedores, Categorías y Productos",
    version="2.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return RedirectResponse(url="/app/login.html")

app.mount("/app", StaticFiles(directory="static", html=True), name="static")

def get_db_connection():
    return db_pool.getconn()

def release_db_connection(conn):
    if db_pool and conn:
        db_pool.putconn(conn)

# ==========================================
# --- MODELOS DE DATOS (PYDANTIC) ---
# ==========================================
class MovimientoCreate(BaseModel):
    id_producto: int
    tipo: str  # 'ENTRADA', 'SALIDA' o 'AJUSTE'
    cantidad: int
    motivo: str | None = None

class CategoriaBase(BaseModel):
    nombre: str
    descripcion: Optional[str] = None

class CategoriaCreate(CategoriaBase):
    pass

class CategoriaUpdate(CategoriaBase):
    pass

class ProveedorBase(BaseModel):
    nombre_empresa: str
    contacto_nombre: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    direccion: Optional[str] = None

class ProveedorCreate(ProveedorBase):
    pass

class ProveedorUpdate(ProveedorBase):
    pass

class ProductoBase(BaseModel):
    nombre_producto: str
    id_categoria: int
    id_proveedor: int
    precio: float
    stock: int
    codigo_barras: Optional[str] = None

class ProductoCreate(ProductoBase):
    pass

class ProductoUpdate(ProductoBase):
    pass

class UsuarioBase(BaseModel):
    nombre: str
    email: str
    rol: str

class UsuarioCreate(BaseModel):
    nombre: str
    email: str
    password: Optional[str] = None
    contrasena: Optional[str] = None
    rol: Optional[str] = "usuario"

class UsuarioUpdate(BaseModel):
    nombre: str
    email: str
    password: Optional[str] = None
    contrasena: Optional[str] = None
    rol: Optional[str] = "usuario"

class LoginRequest(BaseModel):
    username: str
    password: str

# ==========================================
# OBTENER HISTORIAL DE VENTAS (GET)
# ==========================================
@app.get("/ventas", tags=["Ventas"])
def obtener_ventas():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT v.*, u.nombre as nombre_usuario 
                FROM ventas v 
                LEFT JOIN usuarios u ON v.id_usuario = u.id_usuario 
                ORDER BY v.id_venta DESC;
            """)
            return cursor.fetchall()
    except Exception:
        conn.rollback()
        return []
    finally:
        release_db_connection(conn)
# ==========================================
# OBTENER HISTORIAL DE MOVIMIENTOS (GET)
# ==========================================
@app.get("/movimientos", tags=["Movimientos"])
def obtener_movimientos():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT h.id_movimiento, h.id_producto, h.tipo_movimiento, 
                       h.cantidad, h.fecha_movimiento, h.motivo, p.nombre as nombre_producto 
                FROM historial_inventario h
                LEFT JOIN productos p ON h.id_producto = p.id_producto
                ORDER BY h.id_movimiento DESC;
            """)
            return cursor.fetchall()
    except Exception as e:
        if conn and not conn.closed:
            conn.rollback()
        print("❌ Error al consultar movimientos:", str(e))
        return []
    finally:
        if conn:
            release_db_connection(conn)

# ==========================================
# REGISTRAR MOVIMIENTO (POST)
# ==========================================
# ==========================================
# REGISTRAR MOVIMIENTO (POST)
# ==========================================
@app.post("/movimientos", tags=["Movimientos"])
def registrar_movimiento(mov: MovimientoCreate):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # 1. Registrar en la tabla historial_inventario
            cursor.execute("""
                INSERT INTO historial_inventario (id_producto, tipo_movimiento, cantidad, motivo)
                VALUES (%s, UPPER(%s), %s, %s) RETURNING *;
            """, (mov.id_producto, mov.tipo, mov.cantidad, mov.motivo))
            nuevo_mov = cursor.fetchone()

            # 2. Actualizar stock según el tipo exacto
            tipo_upper = mov.tipo.upper()
            
            if tipo_upper == 'ENTRADA':
                cursor.execute("UPDATE productos SET stock_actual = stock_actual + %s WHERE id_producto = %s;", (mov.cantidad, mov.id_producto))
            elif tipo_upper == 'SALIDA':
                cursor.execute("UPDATE productos SET stock_actual = GREATEST(0, stock_actual - %s) WHERE id_producto = %s;", (mov.cantidad, mov.id_producto))
            elif tipo_upper == 'AJUSTE':
                # El ajuste reemplaza directamente el stock actual por la cantidad contada en auditoría
                cursor.execute("UPDATE productos SET stock_actual = %s WHERE id_producto = %s;", (mov.cantidad, mov.id_producto))

            conn.commit()
            return {"mensaje": "Movimiento registrado exitosamente", "movimiento": nuevo_mov}
    except Exception as e:
        if conn and not conn.closed:
            conn.rollback()
        print("❌ Error al registrar movimiento:", str(e))
        raise HTTPException(status_code=500, detail=f"Error al registrar movimiento: {str(e)}")
    finally:
        if conn:
            release_db_connection(conn)
# ==========================================
# --- MÓDULO DE CATEGORÍAS (CRUD) ---
# ==========================================

@app.get("/categorias", tags=["Categorías"])
def obtener_categorias():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM categorias ORDER BY id_categoria ASC;")
            return cursor.fetchall()
    except Exception:
        conn.rollback()
        return []
    finally:
        release_db_connection(conn)

@app.post("/categorias", tags=["Categorías"], status_code=status.HTTP_201_CREATED)
def crear_categoria(cat: CategoriaCreate):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = "INSERT INTO categorias (nombre, descripcion) VALUES (%s, %s) RETURNING *;"
            cursor.execute(query, (cat.nombre, cat.descripcion))
            nueva = cursor.fetchone()
            conn.commit()
            return nueva
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        release_db_connection(conn)

@app.put("/categorias/{id_categoria}", tags=["Categorías"])
def actualizar_categoria(id_categoria: int, cat: CategoriaUpdate):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = "UPDATE categorias SET nombre = %s, descripcion = %s WHERE id_categoria = %s RETURNING *;"
            cursor.execute(query, (cat.nombre, cat.descripcion, id_categoria))
            actualizada = cursor.fetchone()
            if not actualizada:
                raise HTTPException(status_code=404, detail="Categoría no encontrada")
            conn.commit()
            return actualizada
    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        release_db_connection(conn)

@app.delete("/categorias/{id_categoria}", tags=["Categorías"])
def eliminar_categoria(id_categoria: int):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Desvincular productos para no violar la Foreign Key
            cursor.execute("UPDATE productos SET id_categoria = NULL WHERE id_categoria = %s;", (id_categoria,))
            cursor.execute("DELETE FROM categorias WHERE id_categoria = %s RETURNING *;", (id_categoria,))
            eliminada = cursor.fetchone()
            if not eliminada:
                raise HTTPException(status_code=404, detail="Categoría no encontrada")
            conn.commit()
            return {"mensaje": "Categoría eliminada exitosamente", "categoria": eliminada}
    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        release_db_connection(conn)

        # ==========================================
# HISTORIAL EXCLUSIVO DEL VENDEDOR (GET)
# ==========================================
@app.get("/vendedor/mis-ventas", tags=["Vendedor"])
def obtener_mis_ventas():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Selecciona únicamente las SALIDAS registradas en el historial
            cursor.execute("""
                SELECT h.id_movimiento, h.id_producto, h.tipo_movimiento, 
                       h.cantidad, h.fecha_movimiento, h.motivo, p.nombre as nombre_producto 
                FROM historial_inventario h
                LEFT JOIN productos p ON h.id_producto = p.id_producto
                WHERE UPPER(h.tipo_movimiento) = 'SALIDA'
                ORDER BY h.id_movimiento DESC;
            """)
            return cursor.fetchall()
    except Exception as e:
        if conn and not conn.closed:
            conn.rollback()
        print("❌ Error al consultar mis ventas:", str(e))
        return []
    finally:
        if conn:
            release_db_connection(conn)


            # ==========================================
# HISTORIAL EXCLUSIVO DEL ALMACENISTA (GET)
# ==========================================
@app.get("/almacen/mis-movimientos", tags=["Almacén"])
def obtener_movimientos_almacen():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT h.id_movimiento, h.id_producto, h.tipo_movimiento, 
                       h.cantidad, h.fecha_movimiento, h.motivo, p.nombre as nombre_producto 
                FROM historial_inventario h
                LEFT JOIN productos p ON h.id_producto = p.id_producto
                WHERE UPPER(h.tipo_movimiento) IN ('ENTRADA', 'AJUSTE')
                ORDER BY h.id_movimiento DESC;
            """)
            return cursor.fetchall()
    except Exception as e:
        if conn and not conn.closed:
            conn.rollback()
        print("❌ Error al consultar movimientos de almacén:", str(e))
        return []
    finally:
        if conn:
            release_db_connection(conn)
            
# ==========================================
# --- 2. MÓDULO DE PROVEEDORES (CRUD) ---
# ==========================================

@app.get("/proveedores", tags=["Proveedores"])
def obtener_proveedores():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM proveedores ORDER BY 1 ASC;")
            return cursor.fetchall()
    except Exception as e:
        conn.rollback()
        print("Error obteniendo proveedores:", str(e))
        return []
    finally:
        release_db_connection(conn)

@app.post("/proveedores", tags=["Proveedores"], status_code=status.HTTP_201_CREATED)
def crear_proveedor(prov: ProveedorCreate):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
            INSERT INTO proveedores (nombre_empresa, contacto_nombre, telefono, email, direccion)
            VALUES (%s, %s, %s, %s, %s) RETURNING *;
            """
            cursor.execute(query, (prov.nombre_empresa, prov.contacto_nombre, prov.telefono, prov.email, prov.direccion))
            nuevo_proveedor = cursor.fetchone()
            conn.commit()
            return nuevo_proveedor
    except Exception as e:
        conn.rollback()
        print("❌ Error guardando proveedor:", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al guardar proveedor: {str(e)}"
        )
    finally:
        release_db_connection(conn)
# ==========================================
# ACTUALIZAR PROVEEDOR (PUT)
# ==========================================
@app.put("/proveedores/{id_prov}", tags=["Proveedores"])
def actualizar_proveedor(id_prov: int, prov: ProveedorUpdate):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
            UPDATE proveedores 
            SET nombre_empresa = %s, contacto_nombre = %s, telefono = %s, email = %s, direccion = %s 
            WHERE id_proveedor = %s RETURNING *;
            """
            cursor.execute(query, (prov.nombre_empresa, prov.contacto_nombre, prov.telefono, prov.email, prov.direccion, id_prov))
            res = cursor.fetchone()
            
            if not res:
                raise HTTPException(status_code=404, detail="Proveedor no encontrado")
                
            conn.commit()
            return res
    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        conn.rollback()
        print("❌ Error al actualizar proveedor:", str(e))
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")
    finally:
        release_db_connection(conn)


# ==========================================
# ELIMINAR USUARIO (DELETE)
# ==========================================
@app.delete("/usuarios/{id_usr}", tags=["Usuarios"])
def eliminar_usuario(id_usr: int):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # 1. Intentar desvincular de tablas dependientes por si acaso
            try:
                cursor.execute("UPDATE ventas SET id_usuario = NULL WHERE id_usuario = %s;", (id_usr,))
            except Exception:
                conn.rollback()

            # 2. Eliminar usando únicamente 'id_usuario' (sin usar la columna 'id')
            query = "DELETE FROM usuarios WHERE id_usuario = %s RETURNING *;"
            cursor.execute(query, (id_usr,))
            eliminado = cursor.fetchone()

            if not eliminado:
                raise HTTPException(status_code=404, detail="Usuario no encontrado")

            conn.commit()
            print(f"✅ Usuario #{id_usr} eliminado exitosamente")
            return {"mensaje": "Usuario eliminado exitosamente", "usuario": eliminado}

    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        conn.rollback()
        print(f"❌ DETALLE DEL ERROR AL BORRAR USUARIO #{id_usr}:", repr(e))
        raise HTTPException(status_code=500, detail=f"Error al borrar usuario: {str(e)}")
    finally:
        release_db_connection(conn)
# ==========================================
# --- 3. MÓDULO DE PRODUCTOS / INVENTARIO (CRUD) ---
# ==========================================

@app.get("/inventario", tags=["Inventario"])
def obtener_inventario():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
            SELECT 
                p.*,
                COALESCE(c.nombre, c.nombre_categoria, 'Sin Categoría') AS nombre_categoria
            FROM productos p
            LEFT JOIN categorias c ON p.id_categoria = c.id_categoria OR p.id_categoria = c.id
            ORDER BY 1 DESC;
            """
            try:
                cursor.execute(query)
                return {"productos": cursor.fetchall()}
            except Exception:
                conn.rollback()
                cursor.execute("SELECT * FROM productos ORDER BY 1 DESC;")
                return {"productos": cursor.fetchall()}
    except Exception as e:
        conn.rollback()
        print("Error en /inventario:", str(e))
        return {"productos": []}
    finally:
        release_db_connection(conn)
        
# ==========================================
# ENDPOINT CREAR PRODUCTO (POST)
# ==========================================
@app.post("/productos", tags=["Productos"], status_code=status.HTTP_201_CREATED)
def crear_producto(prod: ProductoCreate):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
            INSERT INTO productos (nombre, id_categoria, id_proveedor, precio_venta, stock_actual, codigo_barras)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING *;
            """
            cursor.execute(query, (
                prod.nombre_producto, 
                prod.id_categoria, 
                prod.id_proveedor, 
                prod.precio, 
                prod.stock, 
                prod.codigo_barras
            ))
            nuevo_producto = cursor.fetchone()
            conn.commit()
            print("✅ Producto guardado con código de barras:", nuevo_producto)
            return nuevo_producto
    except Exception as e:
        conn.rollback()
        print("❌ Error guardando producto:", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en base de datos: {str(e)}"
        )
    finally:
        release_db_connection(conn)


# ==========================================
# ENDPOINT ACTUALIZAR PRODUCTO (PUT)
# ==========================================
@app.put("/productos/{id_prod}", tags=["Productos"])
def actualizar_producto(id_prod: int, prod: ProductoUpdate):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
            UPDATE productos 
            SET nombre = %s, id_categoria = %s, id_proveedor = %s, precio_venta = %s, stock_actual = %s, codigo_barras = %s
            WHERE id_producto = %s RETURNING *;
            """
            cursor.execute(query, (
                prod.nombre_producto, 
                prod.id_categoria, 
                prod.id_proveedor, 
                prod.precio, 
                prod.stock, 
                prod.codigo_barras, 
                id_prod
            ))
            res = cursor.fetchone()
            if not res:
                raise HTTPException(status_code=404, detail="Producto no encontrado")
            conn.commit()
            return res
    except Exception as e:
        conn.rollback()
        print("❌ Error al actualizar producto:", str(e))
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")
    finally:
        release_db_connection(conn)


# ==========================================
# ENDPOINT ACTUALIZAR PRODUCTO (PUT)
# ==========================================
@app.put("/productos/{id_prod}", tags=["Productos"])
def actualizar_producto(id_prod: int, prod: ProductoUpdate):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
            UPDATE productos 
            SET nombre = %s, id_categoria = %s, id_proveedor = %s, precio_venta = %s, stock_actual = %s, codigo_barras = %s
            WHERE id_producto = %s RETURNING *;
            """
            cursor.execute(query, (
                prod.nombre_producto, 
                prod.id_categoria, 
                prod.id_proveedor, 
                prod.precio, 
                prod.stock, 
                prod.codigo_barras, 
                id_prod
            ))
            res = cursor.fetchone()
            if not res:
                raise HTTPException(status_code=404, detail="Producto no encontrado")
            conn.commit()
            return res
    except Exception as e:
        conn.rollback()
        print("❌ Error al actualizar producto:", str(e))
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")
    finally:
        release_db_connection(conn)

@app.delete("/productos/{id_prod}", tags=["Productos"])
def eliminar_producto(id_prod: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            try:
                cursor.execute("DELETE FROM productos WHERE id_producto = %s;", (id_prod,))
            except Exception:
                conn.rollback()
                cursor.execute("DELETE FROM productos WHERE id = %s;", (id_prod,))
            conn.commit()
            return {"mensaje": "Producto eliminado exitosamente"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al eliminar producto: {str(e)}")
    finally:
        release_db_connection(conn)

# ==========================================
# --- 4. MÓDULO DE USUARIOS (CRUD) ---
# ==========================================

@app.get("/usuarios", tags=["Usuarios"])
def obtener_usuarios():
    conn = get_db_connection()
    consultas = [
        "SELECT id_usuario, nombre, email, rol FROM usuarios ORDER BY 1 ASC;",
        "SELECT id, nombre, email, rol FROM usuarios ORDER BY 1 ASC;",
        "SELECT id_usuario, nombre_usuario AS nombre, correo_usuario AS email, rol FROM usuario ORDER BY 1 ASC;",
        "SELECT id, nombre, email, rol FROM usuario ORDER BY 1 ASC;",
        "SELECT * FROM usuarios ORDER BY 1 ASC;",
        "SELECT * FROM usuario ORDER BY 1 ASC;"
    ]
    
    for query in consultas:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query)
                res = cursor.fetchall()
                conn.commit()
                return res
        except Exception:
            conn.rollback()
            continue
            
    release_db_connection(conn)
    return []

@app.post("/usuarios", tags=["Usuarios"], status_code=status.HTTP_201_CREATED)
def crear_usuario(usr: UsuarioCreate):
    conn = get_db_connection()
    pass_val = usr.password or usr.contrasena or "123456"
    
    rol_input = (usr.rol or "usuario").lower()
    
    if "admin" in rol_input:
        rol_final = "admin"
    elif "almacen" in rol_input or "editor" in rol_input:
        rol_final = "editor"
    else:
        rol_final = "usuario"

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
            INSERT INTO usuarios (nombre, email, password_hash, rol)
            VALUES (%s, %s, %s, %s) RETURNING *;
            """
            cursor.execute(query, (usr.nombre, usr.email, pass_val, rol_final))
            nuevo_usr = cursor.fetchone()
            conn.commit()
            print(f"✅ Usuario guardado exitosamente con rol '{rol_final}' en Neon Cloud")
            return nuevo_usr

    except Exception as e:
        conn.rollback()
        print("❌ Error guardando usuario:", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en base de datos: {str(e)}"
        )
    finally:
        release_db_connection(conn)

# ==========================================
# ACTUALIZAR USUARIO (PUT)
# ==========================================
@app.put("/usuarios/{id_usr}", tags=["Usuarios"])
def actualizar_usuario(id_usr: int, usr: UsuarioUpdate):
    conn = get_db_connection()
    pass_val = usr.password or usr.contrasena or "123456"
    
    # Mapeo estricto del rol para la restricción CHECK ('admin', 'usuario', 'editor')
    rol_input = (usr.rol or "usuario").lower()
    if "admin" in rol_input:
        rol_final = "admin"
    elif "almacen" in rol_input or "editor" in rol_input:
        rol_final = "editor"
    else:
        rol_final = "usuario"

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # 1. Probar actualización con 'password_hash'
            try:
                query1 = """
                UPDATE usuarios 
                SET nombre = %s, email = %s, password_hash = %s, rol = %s 
                WHERE id_usuario = %s RETURNING *;
                """
                cursor.execute(query1, (usr.nombre, usr.email, pass_val, rol_final, id_usr))
                res = cursor.fetchone()
                if res:
                    conn.commit()
                    print(f"✅ Usuario #{id_usr} actualizado con 'password_hash'")
                    return res
            except Exception:
                conn.rollback()

            # 2. Respaldo por si la columna de contraseña es 'password'
            query2 = """
            UPDATE usuarios 
            SET nombre = %s, email = %s, password = %s, rol = %s 
            WHERE id_usuario = %s RETURNING *;
            """
            cursor.execute(query2, (usr.nombre, usr.email, pass_val, rol_final, id_usr))
            res = cursor.fetchone()
            if not res:
                raise HTTPException(status_code=404, detail="Usuario no encontrado")
            conn.commit()
            print(f"✅ Usuario #{id_usr} actualizado con 'password'")
            return res

    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        conn.rollback()
        print("❌ Error al actualizar usuario:", str(e))
        raise HTTPException(status_code=500, detail=f"Error al actualizar usuario: {str(e)}")
    finally:
        release_db_connection(conn)

@app.delete("/usuarios/{id_usr}", tags=["Usuarios"])
def eliminar_usuario(id_usr: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM usuarios WHERE id_usuario = %s OR id = %s;", (id_usr, id_usr))
            conn.commit()
            return {"mensaje": "Usuario eliminado exitosamente"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al eliminar usuario: {str(e)}")
    finally:
        release_db_connection(conn)

# ==========================================
# --- 5. AUTENTICACIÓN / LOGIN OAUTH2 FORM ---
# ==========================================

@app.post("/login", tags=["Autenticación"])
async def login(request: Request):
    username = ""
    password_ingresada = ""

    try:
        form_data = await request.form()
        username = str(form_data.get("username") or form_data.get("email") or "").strip()
        password_ingresada = str(form_data.get("password") or "").strip()
    except Exception:
        try:
            json_data = await request.json()
            username = str(json_data.get("username") or json_data.get("email") or "").strip()
            password_ingresada = str(json_data.get("password") or "").strip()
        except Exception:
            pass

    if not username or not password_ingresada:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requieren credenciales válidas (correo/usuario y contraseña)"
        )

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = "SELECT * FROM usuarios WHERE LOWER(TRIM(email)) = LOWER(%s) OR LOWER(TRIM(nombre)) = LOWER(%s);"
            cursor.execute(query, (username, username))
            usuario = cursor.fetchone()

            if not usuario:
                conn.rollback()
                query_alt = "SELECT * FROM usuario WHERE LOWER(TRIM(email)) = LOWER(%s) OR LOWER(TRIM(nombre)) = LOWER(%s);"
                try:
                    cursor.execute(query_alt, (username, username))
                    usuario = cursor.fetchone()
                except Exception:
                    conn.rollback()

            if not usuario:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Correo o usuario no encontrado en la base de datos"
                )

            password_bd = str(
                usuario.get("password_hash") or
                usuario.get("password") or 
                usuario.get("contraseña") or 
                usuario.get("contrasena") or 
                usuario.get("clave") or 
                ""
            ).strip()

            if password_bd and password_bd != password_ingresada and password_bd.lower() != password_ingresada.lower():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Contraseña incorrecta"
                )

            rol_detectado = str(usuario.get("rol") or usuario.get("tipo_usuario") or "administrador").lower()
            id_usuario = usuario.get("id_usuario") or usuario.get("id") or 1
            nombre_usuario = usuario.get("nombre") or usuario.get("nombre_usuario") or username
            email_usuario = usuario.get("email") or usuario.get("correo") or username

            return {
                "access_token": f"bearer-token-{id_usuario}",
                "token_type": "bearer",
                "usuario": {
                    "id": id_usuario,
                    "nombre": nombre_usuario,
                    "email": email_usuario,
                    "rol": rol_detectado
                }
            }
    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        release_db_connection(conn)