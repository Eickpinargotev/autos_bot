-- Los mensajes del negocio, editables desde el panel.
--
-- Hasta aquí, los textos de la palabra clave («tareas», «transporte»), sus
-- recordatorios y la bienvenida al grupo vivían SOLO en `mensajes.json`, que es
-- un archivo del repositorio: cambiar una palabra exigía editarlo y redeplegar.
-- El negocio no podía tocar sus propios mensajes.
--
-- Ahora viven en `plantillas_mensaje` con su clave, igual que los de las
-- ciudades, y se editan en «Mensajes». `mensajes.json` sigue siendo el respaldo:
-- si la clave no está en la base, el bot usa la del archivo. Así una base vacía
-- nunca deja al bot mudo.

INSERT INTO plantillas_mensaje (clave, nombre)
VALUES
    ('TAREAS', 'Palabra clave: tareas'),
    ('TRANSPORTE', 'Palabra clave: transporte'),
    ('TAREAS_R1', 'Recordatorio 1 tras «tareas»'),
    ('TAREAS_R2', 'Recordatorio 2 tras «tareas»'),
    ('TAREAS_R3', 'Recordatorio 3 tras «tareas»'),
    ('BIENVENIDA_GRUPO', 'Bienvenida al entrar al grupo')
ON CONFLICT (clave) DO NOTHING;

INSERT INTO plantilla_partes (plantilla_id, orden, texto)
SELECT p.id, v.orden, v.texto
FROM (VALUES
    ('TAREAS', 1, '💡💡💡Hola!!!

Para iniciar con el curso teórico solamente debes ingresar al siguiente enlace 
👇🏻👇🏻👇🏻👇🏻👇🏻
De inmediato comenzarás a recibir el estudio del curso teórico para licencias

Usuario de estudio: (PONER CÉDULA)
Contraseña: (PONER TELÉFONO)

MOTOCICLETA: https://app.escuelasdemanejocr.com/course/view.php?id=24

AUTOMOVIL: https://app.escuelasdemanejocr.com/course/view.php?id=25

Bendiciones'),
    ('TRANSPORTE', 1, '💡💡💡Hola!!!

Para iniciar con el curso teórico de transporte público solamente debes ingresar al siguiente enlace 
👇🏻👇🏻👇🏻👇🏻👇🏻
https://app.escuelasdemanejocr.com/course/view.php?id=22

De inmediato comenzarás a recibir el estudio del curso teórico para transporte público 

Usuario de estudio: (PONER CÉDULA)
Contraseña: (PONER TELÉFONO)

Bendiciones'),
    ('TAREAS_R1', 1, '📌 Pudo ingresar a la página de estudio???'),
    ('TAREAS_R2', 1, '📌 Como va con su programa de estudio???'),
    ('TAREAS_R3', 1, 'Recuerde que puede solicitar su cita teórica en el siguiente enlace

En la pregunta que dice "INGRESO COSEVI" debe poner la clave de su usuario 

Si llena el formulario con los datos incompletos o incorrectos será eliminado de la lista sin previo aviso. 

Es su responsabilidad velar por la veracidad de los datos suministrados

https://forms.gle/AKn9QGgByCEbBDDx8'),
    ('BIENVENIDA_GRUPO', 1, '📲 Gracias por unirse a nuestro grupo del curso teórico!!!

Recuerde:

🎯 Por política de transparencia no cobramos nada antes del curso y pagas en efectivo hasta ese mismo día.

🎯 Traer documento de identidad 

🎯 Traer material para tomar notas (Cuaderno y lapicero) 

Por favor presentarse unos 10 minutos antes para hacer la matrícula e iniciar de la mejor manera la obtención de su licencia.

Bendiciones')
) AS v(clave, orden, texto)
JOIN plantillas_mensaje p ON p.clave = v.clave
WHERE NOT EXISTS (
    SELECT 1 FROM plantilla_partes pp WHERE pp.plantilla_id = p.id AND pp.orden = v.orden
);
